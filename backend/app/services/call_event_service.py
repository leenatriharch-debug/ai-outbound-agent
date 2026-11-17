import logging
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.call import Call
from app.models.call_attempt import CallAttempt
from app.models.call_dispatch_job import CallDispatchJob
from app.models.vonage_webhook_event import VonageWebhookEvent
from app.services.call_lifecycle_service import CallLifecycleService

logger = logging.getLogger(__name__)


class CallEventService:
    STATUS_MAP = {
        "started": "initiated",
        "initiated": "initiated",
        "ringing": "ringing",
        "answered": "answered",
        "machine": "answered",
        "completed": "completed",
        "busy": "busy",
        "timeout": "no_answer",
        "unanswered": "no_answer",
        "no_answer": "no_answer",
        "rejected": "failed",
        "failed": "failed",
        "disconnected": "failed",
        "cancelled": "cancelled",
    }

    @classmethod
    def ingest_event(
        cls,
        db: Session,
        event_id: str,
        payload: dict,
        dispatch_job_id: int | None = None,
    ) -> str:
        stored_payload = {
            "uuid": payload.get("uuid"),
            "status": payload.get("status"),
        }
        event = VonageWebhookEvent(
            event_id=event_id,
            call_uuid=stored_payload["uuid"],
            dispatch_job_id=dispatch_job_id,
            event_status=stored_payload["status"],
            payload=stored_payload,
        )
        db.add(event)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            event = db.query(VonageWebhookEvent).filter(
                VonageWebhookEvent.event_id == event_id
            ).first()
            if not event:
                raise
            if event.processed_at:
                return "duplicate"

        return cls._process_event(db, event)

    @classmethod
    def process_pending_events(
        cls,
        db: Session,
        limit: int = 50,
        call_uuid: str | None = None,
    ) -> int:
        query = db.query(VonageWebhookEvent).filter(
            VonageWebhookEvent.processed_at.is_(None)
        )
        if call_uuid:
            query = query.filter(VonageWebhookEvent.call_uuid == call_uuid)
        else:
            query = query.filter(
                VonageWebhookEvent.processing_error.is_(None)
            )

        events = query.order_by(
            VonageWebhookEvent.received_at.asc()
        ).limit(limit).all()

        processed = 0
        for event in events:
            result = cls._process_event(db, event)
            if result != "pending":
                processed += 1
        return processed

    @classmethod
    def _process_event(cls, db: Session, event: VonageWebhookEvent) -> str:
        payload = event.payload or {}
        provider_status = str(payload.get("status", "")).strip().lower()
        call_uuid = payload.get("uuid")
        new_status = cls.STATUS_MAP.get(provider_status)

        if not new_status:
            event.processed_at = datetime.now(timezone.utc)
            event.processing_error = (
                f"Unsupported Vonage call status: {provider_status or 'missing'}"
            )
            logger.warning(
                "Ignoring unsupported Vonage call status %r for event %s",
                provider_status,
                event.event_id,
            )
            db.commit()
            return "ignored"

        if not call_uuid:
            event.processed_at = datetime.now(timezone.utc)
            event.processing_error = "Webhook event is missing call UUID"
            db.commit()
            return "ignored"

        job = None
        if event.dispatch_job_id is not None:
            job = db.query(CallDispatchJob).filter(
                CallDispatchJob.id == event.dispatch_job_id
            ).with_for_update().first()
            if not job:
                event.processing_error = "Dispatch job is not available yet"
                db.commit()
                return "pending"

        attempt = None
        call = None
        if job and job.call_attempt_id:
            attempt = db.query(CallAttempt).filter(
                CallAttempt.id == job.call_attempt_id
            ).with_for_update().first()
        elif job and job.call_id:
            call = db.query(Call).filter(
                Call.id == job.call_id
            ).with_for_update().first()
        else:
            attempt = db.query(CallAttempt).filter(
                CallAttempt.call_uuid == call_uuid
            ).with_for_update().first()
            if not attempt:
                call = db.query(Call).filter(
                    Call.call_uuid == call_uuid
                ).with_for_update().first()

        target = attempt or call
        if not target:
            event.processing_error = "Waiting for call UUID persistence"
            db.commit()
            return "pending"

        existing_uuid = target.call_uuid
        if existing_uuid and existing_uuid != call_uuid:
            event.processed_at = datetime.now(timezone.utc)
            event.processing_error = "Provider UUID does not match dispatch target"
            logger.error(
                "Vonage UUID mismatch for dispatch job %s",
                event.dispatch_job_id,
            )
            db.commit()
            return "ignored"

        duplicate_attempt_query = db.query(CallAttempt.id).filter(
            CallAttempt.call_uuid == call_uuid
        )
        if attempt:
            duplicate_attempt_query = duplicate_attempt_query.filter(
                CallAttempt.id != attempt.id
            )
        duplicate_attempt = duplicate_attempt_query.first()

        duplicate_call_query = db.query(Call.id).filter(
            Call.call_uuid == call_uuid
        )
        if call:
            duplicate_call_query = duplicate_call_query.filter(
                Call.id != call.id
            )
        duplicate_call = duplicate_call_query.first()
        if duplicate_attempt or duplicate_call:
            event.processed_at = datetime.now(timezone.utc)
            event.processing_error = "Provider UUID is already assigned"
            db.commit()
            return "ignored"

        if job:
            duplicate_job = db.query(CallDispatchJob.id).filter(
                CallDispatchJob.provider_call_uuid == call_uuid,
                CallDispatchJob.id != job.id,
            ).first()
            if duplicate_job:
                event.processed_at = datetime.now(timezone.utc)
                event.processing_error = "Provider UUID is already assigned"
                db.commit()
                return "ignored"
            job.provider_call_uuid = call_uuid
            job.status = "dispatched"
            job.last_error = None

        if attempt:
            attempt.call_uuid = call_uuid
            if attempt.status in {
                "queued",
                "dispatching",
                "dispatch_unknown",
            }:
                CallLifecycleService.update_status(
                    db,
                    attempt.id,
                    "initiated",
                    call_uuid=call_uuid,
                    commit=False,
                )
            self_status = attempt.status
            if not cls._is_stale_status(self_status, new_status):
                CallLifecycleService.update_status(
                    db,
                    attempt.id,
                    new_status,
                    failure_reason=(
                        f"Vonage call status: {provider_status}"
                        if new_status == "failed"
                        else None
                    ),
                    call_uuid=call_uuid,
                    commit=False,
                )
        else:
            cls._update_legacy_call(call, call_uuid, new_status)

        event.processed_at = datetime.now(timezone.utc)
        event.processing_error = None
        db.commit()
        return "updated"

    @staticmethod
    def _is_stale_status(current: str, incoming: str) -> bool:
        order = {
            "queued": 0,
            "dispatching": 0,
            "dispatch_unknown": 0,
            "initiated": 1,
            "ringing": 2,
            "answered": 3,
        }
        return (
            current in order
            and incoming in order
            and order[incoming] < order[current]
        )

    @staticmethod
    def _update_legacy_call(call: Call, call_uuid: str, new_status: str):
        terminal_statuses = {
            "completed",
            "failed",
            "busy",
            "no_answer",
            "cancelled",
        }
        if call.status in terminal_statuses:
            return

        order = {
            "pending": 0,
            "dispatching": 0,
            "dispatch_unknown": 0,
            "initiated": 1,
            "ringing": 2,
            "answered": 3,
        }
        if (
            call.status in order
            and new_status in order
            and order[new_status] < order[call.status]
        ):
            return

        call.call_uuid = call_uuid
        call.status = new_status
        if new_status in terminal_statuses:
            call.completed_at = datetime.now(timezone.utc)

    @staticmethod
    def update_call_status(db: Session, call_uuid: str, status: str):
        call = db.query(Call).filter(Call.call_uuid == call_uuid).first()
        if not call:
            return None
        CallEventService._update_legacy_call(call, call_uuid, status)
        db.commit()
        db.refresh(call)
        return call
