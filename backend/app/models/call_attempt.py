from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    ForeignKey
)
from sqlalchemy.sql import func

from app.database.connection import Base


class CallAttempt(Base):
    __tablename__ = "call_attempts"

    id = Column(Integer, primary_key=True, index=True)

    campaign_contact_id = Column(
        Integer,
        ForeignKey("campaign_contacts.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    call_uuid = Column(
        String(100),
        unique=True,
        nullable=True,
        index=True
    )

    attempt_number = Column(Integer, nullable=False)

    status = Column(
        String(30),
        default="queued",
        nullable=False,
        index=True
    )

    started_at = Column(DateTime(timezone=True), nullable=True)
    answered_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    duration_seconds = Column(Integer, default=0)

    failure_reason = Column(Text, nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now()
    )