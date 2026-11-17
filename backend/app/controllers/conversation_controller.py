from sqlalchemy.orm import Session

from app.services.conversation_manager import ConversationManager


class ConversationController:

    @staticmethod
    def create_session(db: Session, customer_id=None, call_id=None):
        return ConversationManager.create_session(
            db=db,
            customer_id=customer_id,
            call_id=call_id
        )

    @staticmethod
    def get_history(db: Session, session_id: int):
        return ConversationManager.get_conversation_history(
            db=db,
            session_id=session_id
        )

    @staticmethod
    def end_session(db: Session, session_id: int):
        return ConversationManager.end_session(
            db=db,
            session_id=session_id
        )

    @staticmethod
    def add_message(db: Session, session_id: int, role: str, message: str):
        return ConversationManager.add_message(
            db=db,
            session_id=session_id,
            role=role,
            message=message
        )