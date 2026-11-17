from sqlalchemy import Column, ForeignKey, Integer

from app.database.connection import Base


class ConversationAttemptLink(Base):
    __tablename__ = "conversation_attempt_links"

    conversation_session_id = Column(
        Integer,
        ForeignKey("conversation_sessions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    call_attempt_id = Column(
        Integer,
        ForeignKey("call_attempts.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
