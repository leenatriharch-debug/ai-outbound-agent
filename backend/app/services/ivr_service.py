from fastapi import HTTPException
from sqlalchemy.orm import Session, selectinload

from app.models.ivr_menu import IVRMenu
from app.models.ivr_menu_option import IVRMenuOption
from app.schemas.ivr import IVRMenuCreate, IVRMenuUpdate


class IVRService:
    @staticmethod
    def _validate_digits(options):
        digits = [option.digit for option in options]
        if len(digits) != len(set(digits)):
            raise HTTPException(
                status_code=422,
                detail="Each IVR menu option must use a unique digit"
            )

    @staticmethod
    def _replace_options(menu: IVRMenu, options):
        IVRService._validate_digits(options)
        menu.options = [
            IVRMenuOption(
                digit=option.digit,
                label=option.label,
                destination_phone=option.destination_phone
            )
            for option in options
        ]

    @staticmethod
    def create_menu(db: Session, data: IVRMenuCreate):
        IVRService._validate_digits(data.options)
        if data.is_active:
            db.query(IVRMenu).filter(IVRMenu.is_active.is_(True)).update(
                {IVRMenu.is_active: False},
                synchronize_session=False
            )

        menu = IVRMenu(
            name=data.name,
            welcome_message=data.welcome_message,
            invalid_input_message=data.invalid_input_message,
            timeout_message=data.timeout_message,
            max_attempts=data.max_attempts,
            timeout_seconds=data.timeout_seconds,
            is_active=data.is_active
        )
        IVRService._replace_options(menu, data.options)
        db.add(menu)
        db.commit()
        return IVRService.get_menu(db, menu.id)

    @staticmethod
    def list_menus(db: Session):
        return (
            db.query(IVRMenu)
            .options(selectinload(IVRMenu.options))
            .order_by(IVRMenu.id.desc())
            .all()
        )

    @staticmethod
    def get_menu(db: Session, menu_id: int):
        menu = (
            db.query(IVRMenu)
            .options(selectinload(IVRMenu.options))
            .filter(IVRMenu.id == menu_id)
            .first()
        )
        if not menu:
            raise HTTPException(status_code=404, detail="IVR menu not found")
        return menu

    @staticmethod
    def get_active_menu(db: Session):
        return (
            db.query(IVRMenu)
            .options(selectinload(IVRMenu.options))
            .filter(IVRMenu.is_active.is_(True))
            .order_by(IVRMenu.id.desc())
            .first()
        )

    @staticmethod
    def update_menu(db: Session, menu_id: int, data: IVRMenuUpdate):
        menu = IVRService.get_menu(db, menu_id)
        updates = data.model_dump(exclude_unset=True, exclude={"options"})

        if updates.get("is_active") is True:
            db.query(IVRMenu).filter(
                IVRMenu.is_active.is_(True),
                IVRMenu.id != menu_id
            ).update(
                {IVRMenu.is_active: False},
                synchronize_session=False
            )

        for field, value in updates.items():
            setattr(menu, field, value)

        if data.options is not None:
            IVRService._replace_options(menu, data.options)

        db.commit()
        return IVRService.get_menu(db, menu_id)
