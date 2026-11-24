from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.call import Call
from app.models.call_dispatch_job import CallDispatchJob
from app.schemas.call import CallCreate


def create_outbound_call(data: CallCreate, db: Session) -> Call:
    call = Call(
        phone_number=data.phone_number,
        status="pending"
    )
    try:
        db.add(call)
        db.flush()
        db.add(CallDispatchJob(call_id=call.id))
        db.commit()
        db.refresh(call)
    except Exception:
        db.rollback()
        raise

    return call


def get_or_create_inbound_call(
    call_uuid: str,
    phone_number: str,
    db: Session,
) -> Call:
    call = db.query(Call).filter(Call.call_uuid == call_uuid).first()
    if call:
        return call

    call = Call(
        call_uuid=call_uuid,
        phone_number=phone_number,
        status="answered",
    )
    try:
        db.add(call)
        db.commit()
        db.refresh(call)
        return call
    except IntegrityError:
        db.rollback()
        call = db.query(Call).filter(Call.call_uuid == call_uuid).first()
        if call:
            return call
        raise
