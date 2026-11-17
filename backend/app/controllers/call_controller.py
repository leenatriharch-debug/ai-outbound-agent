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
