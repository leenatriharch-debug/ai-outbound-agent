from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    ForeignKey,
    Text,
    UniqueConstraint
)
from sqlalchemy.sql import func

from app.database.connection import Base


class CampaignContact(Base):
    __tablename__ = "campaign_contacts"

    __table_args__ = (
        UniqueConstraint(
            "campaign_id",
            "customer_id",
            name="uq_campaign_customer"
        ),
    )

    id = Column(Integer, primary_key=True, index=True)

    campaign_id = Column(
        Integer,
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        nullable=False
    )

    customer_id = Column(
        Integer,
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False
    )

    status = Column(
        String(30),
        default="pending",
        nullable=False
    )

    attempts = Column(Integer, default=0, nullable=False)

    next_attempt_at = Column(DateTime(timezone=True), nullable=True)

    last_error = Column(Text, nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now()
    )

    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now()
    )