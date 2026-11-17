from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.schemas.call import CallCreate, CallResponse
from app.controllers.call_controller import create_outbound_call
from app.services.auth_service import require_roles

router = APIRouter(
    prefix="/api/calls",
    tags=["Outbound Calls"],
    dependencies=[Depends(require_roles("admin", "operator"))],
)


@router.post("/outbound", response_model=CallResponse)
def start_outbound_call(
    data: CallCreate,
    db: Session = Depends(get_db)
):
    return create_outbound_call(data, db)