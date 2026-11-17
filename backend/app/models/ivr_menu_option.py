from sqlalchemy import Column, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship

from app.database.connection import Base


class IVRMenuOption(Base):
    __tablename__ = "ivr_menu_options"
    __table_args__ = (
        UniqueConstraint("menu_id", "digit", name="uq_ivr_menu_option_digit"),
    )

    id = Column(Integer, primary_key=True, index=True)
    menu_id = Column(
        Integer,
        ForeignKey("ivr_menus.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    digit = Column(String(1), nullable=False)
    label = Column(String(100), nullable=False)
    destination_phone = Column(String(20), nullable=False)

    menu = relationship("IVRMenu", back_populates="options")
