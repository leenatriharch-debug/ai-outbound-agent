from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime
)
from sqlalchemy.sql import func

from app.database.connection import Base


class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(Integer, primary_key=True, index=True)

    name = Column(String(150), nullable=False)
    description = Column(Text, nullable=True)

    status = Column(
        String(30),
        default="draft",
        nullable=False
    )

    scheduled_at = Column(DateTime(timezone=True), nullable=True)

    total_contacts = Column(
        Integer,
        default=0,
        nullable=False
    )

    completed_calls = Column(
        Integer,
        default=0,
        nullable=False
    )

    failed_calls = Column(
        Integer,
        default=0,
        nullable=False
    )

    max_attempts = Column(
        Integer,
        default=3,
        nullable=False
    )

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now()
    )

    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now()
    )