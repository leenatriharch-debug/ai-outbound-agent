from sqlalchemy import Column, Integer, String, Boolean, Text, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database.connection import Base


class IVRMenu(Base):
    __tablename__ = "ivr_menus"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False)
    welcome_message = Column(Text, nullable=False)
    invalid_input_message = Column(
        Text,
        nullable=True
    )
    timeout_message = Column(
        Text,
        nullable=True
    )
    max_attempts = Column(Integer, default=3, nullable=False)
    timeout_seconds = Column(Integer, default=10, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now()
    )
    options = relationship(
        "IVRMenuOption",
        back_populates="menu",
        cascade="all, delete-orphan",
        order_by="IVRMenuOption.digit"
    )