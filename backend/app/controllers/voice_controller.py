import json

from fastapi import Request, Depends
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.services.call_event_service import CallEventService
from app.services.vonage_webhook_security import (
    WebhookVerificationError,
    verify_dispatch_callback,
    verify_vonage_webhook,
)


async def handle_call_event(
    request: Request,
    db: Session = Depends(get_db)
):
    raw_body = await request.body()
    try:
        event_data = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise HTTPException(
            status_code=400,
            detail="Invalid Vonage event payload"
        ) from error
    if not isinstance(event_data, dict):
        raise HTTPException(
            status_code=400,
            detail="Vonage event payload must be a JSON object"
        )
    call_uuid = event_data.get("uuid")
    event_status = event_data.get("status")
    if call_uuid is not None and (
        not isinstance(call_uuid, str) or len(call_uuid) > 100
    ):
        raise HTTPException(
            status_code=400,
            detail="Invalid Vonage call UUID"
        )
    if event_status is not None and (
        not isinstance(event_status, str) or len(event_status) > 50
    ):
        raise HTTPException(
            status_code=400,
            detail="Invalid Vonage call status"
        )

    try:
        claims = verify_vonage_webhook(request, raw_body)
    except WebhookVerificationError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail=str(error)
        ) from error

    dispatch_id_value = request.query_params.get("dispatch_id")
    dispatch_job_id = None
    if dispatch_id_value is not None:
        try:
            dispatch_job_id = int(dispatch_id_value)
        except ValueError as error:
            raise HTTPException(
                status_code=400,
                detail="Invalid dispatch identifier"
            ) from error
        if not verify_dispatch_callback(
            dispatch_job_id,
            request.query_params.get("dispatch_sig", ""),
            "events"
        ):
            raise HTTPException(
                status_code=401,
                detail="Invalid dispatch callback signature"
            )

    result = CallEventService.ingest_event(
        db=db,
        event_id=claims["jti"],
        payload=event_data,
        dispatch_job_id=dispatch_job_id,
    )

    return {
        "status": result,
        "call_uuid": event_data.get("uuid"),
        "call_status": event_data.get("status"),
    }