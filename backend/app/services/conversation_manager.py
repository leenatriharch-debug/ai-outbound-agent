import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.conversation_session import ConversationSession
from app.models.conversation_message import ConversationMessage


class ConversationManager:

    @staticmethod
    def create_session(db: Session, customer_id: int = None, call_id: int = None):
        session = ConversationSession(
            session_id=str(uuid.uuid4()),
            customer_id=customer_id,
            call_id=call_id,
            status="active"
        )

        db.add(session)
        db.commit()
        db.refresh(session)

        return session

    @staticmethod
    def add_message(db: Session, session_id: int, role: str, message: str):
        if role not in ["user", "assistant", "system"]:
            raise ValueError("Invalid message role")

        session = db.query(ConversationSession).filter(
            ConversationSession.id == session_id,
            ConversationSession.status == "active"
        ).first()

        if not session:
            raise ValueError("Active conversation session not found")

        new_message = ConversationMessage(
            session_id=session.id,
            role=role,
            message=message
        )

        db.add(new_message)
        db.commit()
        db.refresh(new_message)

        return new_message

    @staticmethod
    def get_conversation_history(db: Session, session_id: int):
        session = db.query(ConversationSession).filter(
            ConversationSession.id == session_id
        ).first()

        if not session:
            raise ValueError("Conversation session not found")

        return db.query(ConversationMessage).filter(
            ConversationMessage.session_id == session_id
        ).order_by(ConversationMessage.created_at.asc()).all()

    @staticmethod
    def end_session(db: Session, session_id: int):
        session = db.query(ConversationSession).filter(
            ConversationSession.id == session_id,
            ConversationSession.status == "active"
        ).first()

        if not session:
            raise ValueError("Active conversation session not found")

        session.status = "completed"
        session.ended_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(session)

        return session