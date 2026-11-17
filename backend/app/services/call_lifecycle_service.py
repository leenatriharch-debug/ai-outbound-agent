import random
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.call_attempt import CallAttempt
from app.models.campaign_contact import CampaignContact
from app.models.campaign import Campaign


class CallLifecycleService:

    ACTIVE_STATUSES = frozenset({
        "queued",
        "dispatching",
        "dispatch_unknown",
        "initiated",
        "ringing",
        "answered"
    })

    TERMINAL_STATUSES = frozenset({
        "completed",
        "failed",
        "busy",
        "no_answer",
        "cancelled"
    })

    ALLOWED_TRANSITIONS = {
        "queued": {"dispatching", "initiated", "failed", "cancelled"},
        "dispatching": {
            "queued",
            "initiated",
            "dispatch_unknown",
            "failed",
            "cancelled"
        },
        "dispatch_unknown": {
            "initiated",
            "ringing",
            "answered",
            "completed",
            "failed",
            "busy",
            "no_answer",
            "cancelled"
        },
        "initiated": {
            "ringing",
            "answered",
            "completed",
            "failed",
            "busy",
            "no_answer",
            "cancelled"
        },
        "ringing": {
            "answered",
            "completed",
            "failed",
            "busy",
            "no_answer",
            "cancelled"
        },
        "answered": {"completed", "failed", "cancelled"}
    }

    RETRYABLE_STATUSES = {"busy", "no_answer"}
    RETRY_BASE_DELAY_SECONDS = 60
    RETRY_MAX_DELAY_SECONDS = 900
    RETRY_JITTER_RATIO = 0.2

    @staticmethod
    def claim_dispatch(db: Session, attempt_id: int) -> bool:
        claimed = db.query(CallAttempt).filter(
            CallAttempt.id == attempt_id,
            CallAttempt.status == "queued",
        ).update(
            {CallAttempt.status: "dispatching"},
            synchronize_session=False,
        )
        return claimed == 1

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _retry_delay_seconds(attempt_number: int) -> int:
        exponential_delay = min(
            CallLifecycleService.RETRY_BASE_DELAY_SECONDS
            * (2 ** max(0, attempt_number - 1)),
            CallLifecycleService.RETRY_MAX_DELAY_SECONDS
        )
        jitter = random.uniform(
            1 - CallLifecycleService.RETRY_JITTER_RATIO,
            1 + CallLifecycleService.RETRY_JITTER_RATIO
        )
        return int(exponential_delay * jitter)

    @staticmethod
    def update_status(
        db: Session,
        attempt_id: int,
        new_status: str,
        failure_reason: str | None = None,
        retryable: bool = False,
        call_uuid: str | None = None,
        commit: bool = True
    ):
        valid_statuses = (
            CallLifecycleService.ACTIVE_STATUSES
            | CallLifecycleService.TERMINAL_STATUSES
        )
        if new_status not in valid_statuses:
            raise ValueError("Invalid call status")

        attempt = (
            db.query(CallAttempt)
            .filter(CallAttempt.id == attempt_id)
            .with_for_update()
            .first()
        )

        if not attempt:
            return None

        contact = (
            db.query(CampaignContact)
            .filter(
                CampaignContact.id == attempt.campaign_contact_id
            )
            .with_for_update()
            .first()
        )

        if not contact:
            raise ValueError("Campaign contact not found")

        campaign = db.query(Campaign).filter(
            Campaign.id == contact.campaign_id
        ).first()
        if not campaign:
            raise ValueError("Campaign not found")

        old_status = attempt.status

        if old_status in CallLifecycleService.TERMINAL_STATUSES:
            return attempt

        if new_status == old_status:
            if call_uuid and not attempt.call_uuid:
                attempt.call_uuid = call_uuid
            return attempt

        allowed = CallLifecycleService.ALLOWED_TRANSITIONS.get(
            old_status,
            set()
        )
        if new_status not in allowed:
            raise ValueError(
                f"Invalid call status transition: {old_status} -> {new_status}"
            )

        now = datetime.now(timezone.utc)
        attempt.status = new_status
        if call_uuid:
            attempt.call_uuid = call_uuid

        if new_status == "initiated":
            attempt.started_at = attempt.started_at or now

        elif new_status == "answered":
            attempt.answered_at = attempt.answered_at or now

        elif new_status in CallLifecycleService.TERMINAL_STATUSES:
            attempt.completed_at = now
            attempt.failure_reason = failure_reason

            if attempt.started_at:
                attempt.duration_seconds = max(
                    0,
                    int(
                        (
                            now
                            - CallLifecycleService._utc(attempt.started_at)
                        ).total_seconds()
                    )
                )

            if new_status == "completed":
                contact.status = "completed"
                contact.next_attempt_at = None
                contact.last_error = None
                db.query(Campaign).filter(
                    Campaign.id == campaign.id
                ).update(
                    {
                        Campaign.completed_calls:
                            Campaign.completed_calls + 1
                    },
                    synchronize_session=False
                )

            elif new_status == "cancelled":
                contact.status = "cancelled"
                contact.next_attempt_at = None

            else:
                contact.last_error = failure_reason or new_status
                should_retry = (
                    new_status in CallLifecycleService.RETRYABLE_STATUSES
                    or (new_status == "failed" and retryable)
                ) and contact.attempts < campaign.max_attempts

                if should_retry:
                    contact.status = "pending"
                    contact.next_attempt_at = now.replace(
                        microsecond=0
                    ) + timedelta(
                        seconds=CallLifecycleService._retry_delay_seconds(
                            attempt.attempt_number
                        )
                    )
                else:
                    contact.status = "failed"
                    contact.next_attempt_at = None
                    db.query(Campaign).filter(
                        Campaign.id == campaign.id
                    ).update(
                        {
                            Campaign.failed_calls:
                                Campaign.failed_calls + 1
                        },
                        synchronize_session=False
                    )
            CallLifecycleService.update_campaign_completion(
                db,
                campaign.id
            )

        else:
            contact.status = "calling"

        try:
            if commit:
                db.commit()
        except Exception:
            db.rollback()
            raise
        if commit:
            db.refresh(attempt)

        return attempt

    @staticmethod
    def update_campaign_completion(db: Session, campaign_id: int):
        db.flush()
        campaign = db.query(Campaign).filter(
            Campaign.id == campaign_id
        ).with_for_update().first()
        if not campaign or campaign.status != "active":
            return

        unfinished_contacts = db.query(CampaignContact.id).filter(
            CampaignContact.campaign_id == campaign_id,
            CampaignContact.status.in_(("pending", "calling"))
        ).first()

        if unfinished_contacts is None:
            campaign.status = "completed"