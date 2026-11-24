from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.schemas.ivr import IVRMenuCreate, IVRMenuResponse, IVRMenuUpdate
from app.services.ivr_service import IVRService
from app.services.auth_service import require_roles


router = APIRouter(
    prefix="/api/ivr/menus",
    tags=["IVR"],
    dependencies=[Depends(require_roles("admin"))],
)


@router.post(
    "/",
    response_model=IVRMenuResponse,
    status_code=status.HTTP_201_CREATED
)
def create_menu(data: IVRMenuCreate, db: Session = Depends(get_db)):
    return IVRService.create_menu(db, data)


@router.get("/", response_model=list[IVRMenuResponse])
def list_menus(db: Session = Depends(get_db)):
    return IVRService.list_menus(db)


@router.get("/{menu_id}", response_model=IVRMenuResponse)
def get_menu(menu_id: int, db: Session = Depends(get_db)):
    return IVRService.get_menu(db, menu_id)


@router.put("/{menu_id}", response_model=IVRMenuResponse)
def update_menu(
    menu_id: int,
    data: IVRMenuUpdate,
    db: Session = Depends(get_db)
):
    return IVRService.update_menu(db, menu_id, data)
