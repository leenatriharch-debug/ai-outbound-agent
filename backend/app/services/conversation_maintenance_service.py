import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.conversation_attempt_link import ConversationAttemptLink
from app.models.conversation_message import ConversationMessage
from app.models.conversation_session import ConversationSession

logger = logging.getLogger(__name__)


class ConversationMaintenanceService:
    @staticmethod
    def recover_stale_sessions(
        db: Session,
        stale_minutes: int = settings.STALE_CONVERSATION_MINUTES,
    ) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)
        sessions = db.query(ConversationSession).filter(
            ConversationSession.status == "active",
            ConversationSession.started_at < cutoff,
        ).with_for_update().all()
        if not sessions:
            return 0

        now = datetime.now(timezone.utc)
        for session in sessions:
            session.status = "failed"
            session.ended_at = now
        db.commit()
        logger.warning(
            "Marked stale conversations as failed",
            extra={"event": "stale_conversations_recovered", "count": len(sessions)},
        )
        return len(sessions)

    @staticmethod
    def purge_expired_transcripts(
        db: Session,
        retention_days: int = settings.TRANSCRIPT_RETENTION_DAYS,
    ) -> int:
        if retention_days < 1:
            raise ValueError("Transcript retention must be at least one day")

        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        expired_ids = [
            row.id
            for row in db.query(ConversationSession.id).filter(
                ConversationSession.started_at < cutoff,
                ConversationSession.status != "active",
            ).all()
        ]
        if not expired_ids:
            return 0

        db.query(ConversationMessage).filter(
            ConversationMessage.session_id.in_(expired_ids)
        ).delete(synchronize_session=False)
        db.query(ConversationAttemptLink).filter(
            ConversationAttemptLink.conversation_session_id.in_(expired_ids)
        ).delete(synchronize_session=False)
        deleted = db.query(ConversationSession).filter(
            ConversationSession.id.in_(expired_ids)
        ).delete(synchronize_session=False)
        db.commit()
        logger.info(
            "Purged expired conversation transcripts",
            extra={"event": "transcripts_purged", "count": deleted},
        )
        return deleted
