import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config.settings import settings
from app.models.call import Call
from app.models.call_attempt import CallAttempt
from app.models.call_dispatch_job import CallDispatchJob
from app.models.campaign_contact import CampaignContact
from app.models.customer import Customer
from app.services.call_event_service import CallEventService
from app.services.call_lifecycle_service import CallLifecycleService
from app.services.provider_errors import (
    ProviderCallRejected,
    ProviderConfigurationError,
)
from app.services.provider_registry import get_provider_registry
from app.services.telephony_provider import CallRequest
from app.services.vonage_webhook_security import build_callback_urls

logger = logging.getLogger(__name__)


class CallDispatchService:
    DISPATCH_LEASE = timedelta(minutes=2)

    @classmethod
    def dispatch_next(
        cls,
        db: Session,
        provider=None,
        base_url: str | None = None,
        signature_secret: str | None = None,
    ) -> bool:
        job = (
            db.query(CallDispatchJob)
            .filter(CallDispatchJob.status == "queued")
            .order_by(CallDispatchJob.created_at.asc(), CallDispatchJob.id.asc())
            .with_for_update(skip_locked=True)
            .first()
        )
        if not job:
            return False

        base_url = settings.PUBLIC_BASE_URL if base_url is None else base_url
        signature_secret = (
            settings.VONAGE_SIGNATURE_SECRET
            if signature_secret is None
            else signature_secret
        )
        if not base_url or not signature_secret:
            raise ProviderConfigurationError(
                "PUBLIC_BASE_URL and VONAGE_SIGNATURE_SECRET are required "
                "before dispatching calls"
            )

        if provider is None:
            provider = get_provider_registry().get_telephony_provider()

        answer_url, event_url = build_callback_urls(
            base_url,
            job.id,
            signature_secret,
        )
        phone_number, attempt, call = cls._load_target(db, job)
        if not phone_number:
            return False

        claimed_at = datetime.now(timezone.utc)
        claim_count = db.query(CallDispatchJob).filter(
            CallDispatchJob.id == job.id,
            CallDispatchJob.status == "queued",
        ).update(
            {
                CallDispatchJob.status: "dispatching",
                CallDispatchJob.attempt_count:
                    CallDispatchJob.attempt_count + 1,
                CallDispatchJob.claimed_at: claimed_at,
                CallDispatchJob.last_error: None,
            },
            synchronize_session=False,
        )
        if claim_count != 1:
            db.rollback()
            return False

        if attempt:
            if not CallLifecycleService.claim_dispatch(db, attempt.id):
                db.rollback()
                return False
        else:
            call_claim_count = db.query(Call).filter(
                Call.id == call.id,
                Call.status == "pending",
            ).update(
                {Call.status: "dispatching"},
                synchronize_session=False,
            )
            if call_claim_count != 1:
                db.rollback()
                return False

        db.commit()

        provider_started = time.perf_counter()
        try:
            result = provider.create_call(
                CallRequest(
                    phone_number=phone_number,
                    answer_url=answer_url,
                    event_url=event_url,
                )
            )
            if not result.call_uuid:
                raise RuntimeError("Telephony provider returned no call UUID")
        except ProviderCallRejected as error:
            logger.info(
                "Telephony provider dispatch request completed",
                extra={
                    "event": "provider_dispatch",
                    "dispatch_job_id": job.id,
                    "call_attempt_id": attempt.id if attempt else None,
                    "provider": settings.TELEPHONY_PROVIDER,
                    "outcome": "rejected",
                    "duration_ms": round(
                        (time.perf_counter() - provider_started) * 1000,
                        2,
                    ),
                },
            )
            cls._mark_rejected(db, job.id, error)
            return True
        except Exception as error:
            logger.info(
                "Telephony provider dispatch request completed",
                extra={
                    "event": "provider_dispatch",
                    "dispatch_job_id": job.id,
                    "call_attempt_id": attempt.id if attempt else None,
                    "provider": settings.TELEPHONY_PROVIDER,
                    "outcome": "unknown",
                    "duration_ms": round(
                        (time.perf_counter() - provider_started) * 1000,
                        2,
                    ),
                },
            )
            cls._mark_unknown(db, job.id, error)
            return True

        logger.info(
            "Telephony provider dispatch request completed",
            extra={
                "event": "provider_dispatch",
                "dispatch_job_id": job.id,
                "call_attempt_id": attempt.id if attempt else None,
                "provider": settings.TELEPHONY_PROVIDER,
                "outcome": "accepted",
                "duration_ms": round(
                    (time.perf_counter() - provider_started) * 1000,
                    2,
                ),
            },
        )
        cls._mark_dispatched(db, job.id, result.call_uuid)
        CallEventService.process_pending_events(
            db,
            call_uuid=result.call_uuid,
        )
        return True

    @staticmethod
    def _load_target(db: Session, job: CallDispatchJob):
        if job.call_attempt_id:
            attempt = db.query(CallAttempt).filter(
                CallAttempt.id == job.call_attempt_id
            ).with_for_update().first()
            if not attempt or attempt.status != "queued":
                job.status = "failed"
                job.last_error = "Call attempt is no longer queued"
                db.commit()
                return None, None, None

            contact = db.query(CampaignContact).filter(
                CampaignContact.id == attempt.campaign_contact_id
            ).first()
            customer = db.query(Customer).filter(
                Customer.id == contact.customer_id
            ).first() if contact else None
            if not customer or not customer.is_active:
                CallLifecycleService.update_status(
                    db,
                    attempt.id,
                    "failed",
                    failure_reason="Customer is unavailable",
                    commit=False,
                )
                job.status = "failed"
                job.last_error = "Customer is unavailable"
                db.commit()
                return None, None, None
            return customer.phone_number, attempt, None

        call = db.query(Call).filter(
            Call.id == job.call_id
        ).with_for_update().first()
        if not call or call.status != "pending":
            job.status = "failed"
            job.last_error = "Call is no longer pending"
            db.commit()
            return None, None, None
        return call.phone_number, None, call

    @classmethod
    def _mark_rejected(
        cls,
        db: Session,
        job_id: int,
        error: ProviderCallRejected,
    ):
        db.expire_all()
        job = db.query(CallDispatchJob).filter(
            CallDispatchJob.id == job_id
        ).with_for_update().first()
        if not job:
            raise ValueError(f"Call dispatch job {job_id} disappeared")

        if job.provider_call_uuid:
            db.rollback()
            return

        job.status = "failed"
        job.last_error = str(error)
        if job.call_attempt_id:
            attempt = db.query(CallAttempt).filter(
                CallAttempt.id == job.call_attempt_id
            ).with_for_update().first()
            if not attempt:
                job.last_error = "Dispatch target was not found"
                db.commit()
                return
            CallLifecycleService.update_status(
                db,
                attempt.id,
                "failed",
                failure_reason=str(error),
                retryable=error.retryable,
                commit=False,
            )
        elif job.call_id:
            call = db.query(Call).filter(
                Call.id == job.call_id
            ).with_for_update().first()
            if not call:
                job.last_error = "Dispatch target was not found"
                db.commit()
                return
            call.status = "failed"
            call.completed_at = datetime.now(timezone.utc)
        db.commit()

    @classmethod
    def _mark_unknown(cls, db: Session, job_id: int, error: Exception):
        logger.warning(
            "Provider dispatch ended with uncertain outcome (%s)",
            type(error).__name__,
        )
        db.expire_all()
        job = db.query(CallDispatchJob).filter(
            CallDispatchJob.id == job_id
        ).with_for_update().first()
        if not job:
            raise ValueError(f"Call dispatch job {job_id} disappeared")
        if job.provider_call_uuid:
            db.rollback()
            return

        job.status = "unknown"
        job.last_error = "Provider outcome unknown; manual reconciliation required"
        if job.call_attempt_id:
            attempt = db.query(CallAttempt).filter(
                CallAttempt.id == job.call_attempt_id
            ).with_for_update().first()
            if attempt and attempt.status == "dispatching":
                CallLifecycleService.update_status(
                    db,
                    attempt.id,
                    "dispatch_unknown",
                    failure_reason=job.last_error,
                    commit=False,
                )
        elif job.call_id:
            call = db.query(Call).filter(
                Call.id == job.call_id
            ).with_for_update().first()
            if call and call.status == "dispatching":
                call.status = "dispatch_unknown"
        db.commit()

    @classmethod
    def _mark_dispatched(
        cls,
        db: Session,
        job_id: int,
        call_uuid: str,
    ):
        db.expire_all()
        job = db.query(CallDispatchJob).filter(
            CallDispatchJob.id == job_id
        ).with_for_update().first()
        if not job:
            raise ValueError(f"Call dispatch job {job_id} disappeared")

        target = None
        if job.call_attempt_id:
            target = db.query(CallAttempt).filter(
                CallAttempt.id == job.call_attempt_id
            ).with_for_update().first()
        elif job.call_id:
            target = db.query(Call).filter(
                Call.id == job.call_id
            ).with_for_update().first()

        if not target:
            job.status = "unknown"
            job.last_error = "Dispatch target disappeared after provider accepted call"
            db.commit()
            return

        if target.call_uuid and target.call_uuid != call_uuid:
            job.status = "unknown"
            job.last_error = "Provider UUID conflicts with an earlier webhook"
            db.commit()
            return

        duplicate_job = db.query(CallDispatchJob.id).filter(
            CallDispatchJob.provider_call_uuid == call_uuid,
            CallDispatchJob.id != job.id,
        ).first()
        duplicate_attempt_query = db.query(CallAttempt.id).filter(
            CallAttempt.call_uuid == call_uuid
        )
        if isinstance(target, CallAttempt):
            duplicate_attempt_query = duplicate_attempt_query.filter(
                CallAttempt.id != target.id
            )
        duplicate_attempt = duplicate_attempt_query.first()

        duplicate_call_query = db.query(Call.id).filter(
            Call.call_uuid == call_uuid
        )
        if isinstance(target, Call):
            duplicate_call_query = duplicate_call_query.filter(
                Call.id != target.id
            )
        duplicate_call = duplicate_call_query.first()
        if duplicate_job or duplicate_attempt or duplicate_call:
            job.status = "unknown"
            job.last_error = "Provider UUID is already assigned to another call"
            db.commit()
            return

        target.call_uuid = call_uuid
        job.provider_call_uuid = call_uuid
        job.status = "dispatched"
        job.last_error = None

        if isinstance(target, CallAttempt):
            if target.status in {"queued", "dispatching", "dispatch_unknown"}:
                CallLifecycleService.update_status(
                    db,
                    target.id,
                    "initiated",
                    call_uuid=call_uuid,
                    commit=False,
                )
        elif target.status in {"pending", "dispatching", "dispatch_unknown"}:
            target.status = "initiated"

        db.commit()

    @classmethod
    def recover_stale_dispatches(cls, db: Session) -> int:
        cutoff = datetime.now(timezone.utc) - cls.DISPATCH_LEASE
        jobs = db.query(CallDispatchJob).filter(
            CallDispatchJob.status == "dispatching",
            CallDispatchJob.claimed_at < cutoff,
        ).with_for_update(skip_locked=True).all()

        for job in jobs:
            target = None
            if job.call_attempt_id:
                target = db.query(CallAttempt).filter(
                    CallAttempt.id == job.call_attempt_id
                ).with_for_update().first()
            elif job.call_id:
                target = db.query(Call).filter(
                    Call.id == job.call_id
                ).with_for_update().first()

            if target and target.call_uuid:
                job.provider_call_uuid = target.call_uuid
                job.status = "dispatched"
                if isinstance(target, CallAttempt) and target.status in {
                    "queued",
                    "dispatching",
                    "dispatch_unknown",
                }:
                    CallLifecycleService.update_status(
                        db,
                        target.id,
                        "initiated",
                        call_uuid=target.call_uuid,
                        commit=False,
                    )
                elif isinstance(target, Call) and target.status in {
                    "pending",
                    "dispatching",
                    "dispatch_unknown",
                }:
                    target.status = "initiated"
                continue

            job.status = "unknown"
            job.last_error = (
                "Worker lease expired before provider outcome was saved; "
                "manual reconciliation required"
            )
            if isinstance(target, CallAttempt) and target.status == "dispatching":
                CallLifecycleService.update_status(
                    db,
                    target.id,
                    "dispatch_unknown",
                    failure_reason=job.last_error,
                    commit=False,
                )
            elif isinstance(target, Call) and target.status == "dispatching":
                target.status = "dispatch_unknown"

        if jobs:
            db.commit()
        return len(jobs)
