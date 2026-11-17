from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.sql import func

from app.database.connection import Base


class CallDispatchJob(Base):
    __tablename__ = "call_dispatch_jobs"
    __table_args__ = (
        CheckConstraint(
            "(call_id IS NOT NULL AND call_attempt_id IS NULL) OR "
            "(call_id IS NULL AND call_attempt_id IS NOT NULL)",
            name="ck_call_dispatch_one_target",
        ),
        Index("ix_call_dispatch_ready", "status", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    call_id = Column(
        Integer,
        ForeignKey("calls.id", ondelete="CASCADE"),
        nullable=True,
        unique=True,
    )
    call_attempt_id = Column(
        Integer,
        ForeignKey("call_attempts.id", ondelete="CASCADE"),
        nullable=True,
        unique=True,
    )
    status = Column(String(30), nullable=False, default="queued", index=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    claimed_at = Column(DateTime(timezone=True), nullable=True)
    provider_call_uuid = Column(String(100), nullable=True, unique=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
