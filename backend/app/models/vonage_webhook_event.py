from sqlalchemy import Column, DateTime, Index, Integer, JSON, String, Text
from sqlalchemy.sql import func

from app.database.connection import Base


class VonageWebhookEvent(Base):
    __tablename__ = "vonage_webhook_events"
    __table_args__ = (
        Index("ix_vonage_webhook_pending", "processed_at", "received_at"),
    )

    event_id = Column(String(100), primary_key=True)
    call_uuid = Column(String(100), nullable=True, index=True)
    dispatch_job_id = Column(Integer, nullable=True, index=True)
    event_status = Column(String(50), nullable=True)
    payload = Column(JSON, nullable=False)
    received_at = Column(DateTime(timezone=True), server_default=func.now())
    processed_at = Column(DateTime(timezone=True), nullable=True)
    processing_error = Column(Text, nullable=True)
