import json
import logging
import uuid

from fastapi import (
    APIRouter,
    Request,
    Depends,
    HTTPException,
    UploadFile,
    File,
    Query,
    WebSocket,
)

from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.database.connection import get_db
from app.database.connection import SessionLocal
from app.models.call import Call
from app.models.call_attempt import CallAttempt
from app.models.call_dispatch_job import CallDispatchJob
from app.controllers.voice_controller import handle_call_event
from app.config.settings import settings
from app.services.ivr_service import IVRService
from app.services.ai_conversation_service import AIConversationService
from app.services.provider_registry import (
    get_provider_registry
)
from app.services.provider_errors import ProviderConfigurationError
from app.services.auth_service import require_roles
from app.services.live_call_service import (
    LiveCallConversationService,
    VonageMediaStreamHandler,
)
from app.services.voice_speech_service import VoiceSpeechService
from app.services.vonage_webhook_security import (
    WebhookVerificationError,
    build_callback_urls,
    build_media_websocket_url,
    verify_dispatch_callback,
    verify_vonage_webhook,
)

SUPPORTED_AUDIO_TYPES = {
    "audio/flac",
    "audio/mpeg",
    "audio/mp3",
    "audio/ogg",
    "audio/wav",
    "audio/webm",
    "audio/x-wav",
    "application/octet-stream",
}


router = APIRouter(
    prefix="/api/voice",
    tags=["Voice"]
)
logger = logging.getLogger(__name__)

class AIMessage(BaseModel):
    message: str


async def _read_audio_upload(file: UploadFile) -> bytes:
    if file.content_type not in SUPPORTED_AUDIO_TYPES:
        raise HTTPException(
            status_code=415,
            detail="Unsupported audio media type",
        )
    audio = await file.read(settings.MAX_AUDIO_UPLOAD_BYTES + 1)
    if len(audio) > settings.MAX_AUDIO_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail="Audio upload exceeds the configured size limit",
        )
    if not audio:
        raise HTTPException(status_code=400, detail="Audio file is empty")
    return audio


def _get_ai_service() -> AIConversationService:
    try:
        provider = get_provider_registry().get_llm_provider()
    except ProviderConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return AIConversationService(provider)


def _get_stt_service():
    try:
        return get_provider_registry().get_stt_processing_service()
    except ProviderConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


def _get_tts_service():
    try:
        return get_provider_registry().get_tts_processing_service()
    except ProviderConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


def _ivr_prompt(menu, base_url: str, attempt: int, message: str | None = None):
    actions = []
    prompt = message or menu.welcome_message
    if prompt:
        actions.append({"action": "talk", "text": prompt})

    event_url = (
        f"{base_url.rstrip('/')}/api/voice/ivr/input"
        f"?menu_id={menu.id}&attempt={attempt}"
    )
    actions.append({
        "action": "input",
        "type": ["dtmf"],
        "dtmf": {
            "maxDigits": 1,
            "timeOut": menu.timeout_seconds
        },
        "eventUrl": [event_url]
    })
    return actions


@router.get("/answer")
async def answer_call(
    request: Request,
    dispatch_id: int | None = Query(default=None, ge=1),
    dispatch_sig: str | None = Query(default=None),
    db: Session = Depends(get_db)
):
    if dispatch_id is not None:
        if not verify_dispatch_callback(
            dispatch_id,
            dispatch_sig or "",
            "answer"
        ):
            raise HTTPException(
                status_code=401,
                detail="Invalid dispatch callback signature"
            )
        try:
            verify_vonage_webhook(request)
        except WebhookVerificationError as error:
            raise HTTPException(
                status_code=error.status_code,
                detail=str(error)
            ) from error
        job = db.query(CallDispatchJob).filter(
            CallDispatchJob.id == dispatch_id
        ).first()
        if not job or job.status not in {
            "dispatching",
            "dispatched",
            "unknown",
        }:
            raise HTTPException(
                status_code=404,
                detail="Outbound call dispatch was not found or is inactive",
            )
        _, event_url = build_callback_urls(
            settings.PUBLIC_BASE_URL,
            dispatch_id,
            settings.VONAGE_SIGNATURE_SECRET,
        )
        media_url = build_media_websocket_url(
            settings.PUBLIC_BASE_URL,
            dispatch_id,
            settings.VONAGE_SIGNATURE_SECRET,
        )
        return [{
            "action": "connect",
            "eventType": "synchronous",
            "eventUrl": [event_url],
            "endpoint": [{
                "type": "websocket",
                "uri": media_url,
                "content-type": "audio/l16;rate=16000",
                "authorization": {"type": "vonage"},
            }],
        }]
    elif request.query_params.get("uuid"):
        try:
            verify_vonage_webhook(request)
        except WebhookVerificationError as error:
            raise HTTPException(
                status_code=error.status_code,
                detail=str(error)
            ) from error

    menu = IVRService.get_active_menu(db)
    if not menu:
        return [{"action": "talk", "text": "Hello! Welcome to Worldotech."}]

    if not settings.PUBLIC_BASE_URL:
        raise HTTPException(
            status_code=503,
            detail="PUBLIC_BASE_URL must be configured before enabling an IVR menu"
        )

    return _ivr_prompt(menu, settings.PUBLIC_BASE_URL, attempt=1)


@router.websocket("/media")
async def media_stream(
    websocket: WebSocket,
    dispatch_id: int = Query(gt=0),
    dispatch_sig: str = Query(),
):
    if not verify_dispatch_callback(dispatch_id, dispatch_sig, "media"):
        logger.warning(
            "Rejected media WebSocket with invalid dispatch signature"
        )
        await websocket.close(code=1008, reason="Invalid dispatch signature")
        return

    try:
        verify_vonage_webhook(websocket)
    except WebhookVerificationError as error:
        logger.warning(
            "Rejected media WebSocket with invalid Vonage authorization: %s",
            error,
        )
        await websocket.close(code=1008, reason="Invalid Vonage authorization")
        return

    db = SessionLocal()
    try:
        job = db.query(CallDispatchJob).filter(
            CallDispatchJob.id == dispatch_id
        ).first()
        if not job or job.status not in {
            "dispatching",
            "dispatched",
            "unknown",
        }:
            await websocket.close(code=1008, reason="Inactive call dispatch")
            return

        call_uuid = job.provider_call_uuid
        if job.call_attempt_id:
            attempt = db.query(CallAttempt).filter(
                CallAttempt.id == job.call_attempt_id
            ).first()
            call_uuid = call_uuid or (attempt.call_uuid if attempt else None)
        elif job.call_id:
            call = db.query(Call).filter(Call.id == job.call_id).first()
            call_uuid = call_uuid or (call.call_uuid if call else None)
    finally:
        db.close()

    try:
        registry = get_provider_registry()
        conversation = LiveCallConversationService(dispatch_id)
        handler = VonageMediaStreamHandler(
            conversation=conversation,
            telephony_provider=registry.get_telephony_provider(),
            call_uuid=call_uuid,
        )
    except ProviderConfigurationError:
        logger.exception("Could not configure live voice providers")
        await websocket.close(code=1011, reason="Voice providers are unavailable")
        return

    await handler.run(websocket)


@router.post("/ivr/input")
async def ivr_input(
    request: Request,
    menu_id: int = Query(gt=0),
    attempt: int = Query(default=1, ge=1),
    db: Session = Depends(get_db)
):
    raw_body = await request.body()
    try:
        verify_vonage_webhook(request, raw_body)
    except WebhookVerificationError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail=str(error)
        ) from error

    menu = IVRService.get_menu(db, menu_id)
    try:
        event = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise HTTPException(
            status_code=400,
            detail="Invalid IVR input payload"
        ) from error
    digit = str(event.get("dtmf", "")).strip()
    option = next((item for item in menu.options if item.digit == digit), None)

    if option:
        actions = [{"action": "talk", "text": f"Connecting you to {option.label}."}]
        connect = {
            "action": "connect",
            "endpoint": [{
                "type": "phone",
                "number": option.destination_phone.lstrip("+")
            }]
        }
        if settings.VONAGE_NUMBER:
            connect["from"] = settings.VONAGE_NUMBER.lstrip("+")
        actions.append(connect)
        return actions

    if attempt >= menu.max_attempts:
        message = menu.timeout_message if not digit else menu.invalid_input_message
        return [{"action": "talk", "text": message or "Goodbye."}]

    message = menu.invalid_input_message if digit else menu.timeout_message
    return _ivr_prompt(
        menu,
        settings.PUBLIC_BASE_URL or str(request.base_url),
        attempt + 1,
        message
    )


@router.post("/events")
async def call_events(
    request: Request,
    db: Session = Depends(get_db)
):
    result = await handle_call_event(request, db)
    if str(result.get("call_status", "")).lower() in {
        "unanswered",
        "failed",
        "disconnected",
        "timeout",
    }:
        return [{
            "action": "talk",
            "text": (
                "We're sorry, but we are unable to continue this call. "
                "Goodbye."
            ),
        }]
    return result


@router.post(
    "/ai/chat",
    dependencies=[Depends(require_roles("admin", "operator"))],
)
async def ai_chat(data: AIMessage):

    service = _get_ai_service()

    if not data.message.strip():
        raise HTTPException(
            status_code=400,
            detail="Message cannot be empty"
        )

    try:
        reply = await service.generate_response(data.message)

        return {
            "status": "success",
            "user_message": data.message,
            "ai_response": reply
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


@router.post(
    "/ai/voice",
    dependencies=[Depends(require_roles("admin", "operator"))],
)
async def ai_voice(data: AIMessage):

    service = _get_ai_service()

    if not data.message.strip():
        raise HTTPException(
            status_code=400,
            detail="Message cannot be empty"
        )

    try:
        reply = await service.generate_response(data.message)

        audio_path = await VoiceSpeechService.generate_audio(
            reply,
            speech_service=_get_tts_service()
        )

        return FileResponse(
            path=audio_path,
            media_type="audio/mpeg",
            filename="ai_response.mp3"
        )

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


@router.post(
    "/ai/stt",
    dependencies=[Depends(require_roles("admin", "operator"))],
)
async def speech_to_text(file: UploadFile = File(...)):

    service = _get_stt_service()
    audio = await _read_audio_upload(file)

    try:
        result = await service.convert_audio_to_text(
            audio,
            file.filename or "audio.wav"
        )

        return {
            "status": "success",
            "transcription": result.text
        }

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


@router.post(
    "/ai/conversation",
    dependencies=[Depends(require_roles("admin", "operator"))],
)
async def ai_conversation(file: UploadFile = File(...)):

    service = _get_ai_service()
    audio = await _read_audio_upload(file)

    try:
        stt_service = _get_stt_service()
        transcription = await stt_service.convert_audio_to_text(
            audio,
            file.filename or "audio.wav"
        )

        if not transcription.text.strip():
            raise HTTPException(
                status_code=400,
                detail="Could not understand the audio"
            )

        reply = await service.generate_response(transcription.text)

        filename = f"conversation_{uuid.uuid4().hex}.mp3"

        audio_path = await VoiceSpeechService.generate_audio(
            reply,
            filename,
            speech_service=_get_tts_service()
        )

        return FileResponse(
            path=audio_path,
            media_type="audio/mpeg",
            filename=filename
        )

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )