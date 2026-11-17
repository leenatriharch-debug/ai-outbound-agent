import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Response
from sqlalchemy import func, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.database.connection import get_db
from app.middleware import RequestContextMiddleware
from app.models.call import Call
from app.models.call_attempt import CallAttempt
from app.models.call_dispatch_job import CallDispatchJob
from app.models.conversation_session import ConversationSession
from app.models.user_account import UserAccount
from app.models.vonage_webhook_event import VonageWebhookEvent
from app.observability import configure_logging
from app.routes.auth_routes import router as auth_router
from app.routes.call_routes import router as call_router
from app.routes.voice_routes import router as voice_router
from app.routes.customer_routes import router as customer_router
from app.routes.campaign_routes import router as campaign_router
from app.routes.campaign_contact_routes import router as campaign_contact_router
from app.routes.conversation_routes import router as conversation_router
from app.routes.ivr_routes import router as ivr_router
from app.services.auth_service import require_roles, validate_production_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging()
    validate_production_settings()
    yield

app = FastAPI(
    title="AI Outbound Calling Agent",
    description="AI-powered outbound calling and campaign management system",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(RequestContextMiddleware)
app.include_router(auth_router)
app.include_router(call_router)
app.include_router(voice_router)
app.include_router(customer_router)
app.include_router(campaign_router)
app.include_router(campaign_contact_router)
app.include_router(conversation_router)
app.include_router(ivr_router)


@app.get("/")
def home():
    return {
        "message": "AI Outbound Calling Agent Backend is Running",
        "status": "success"
    }


@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": "AI Outbound Calling Agent"
    }


@app.get("/ready")
def readiness_check(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        if settings.APP_ENV.lower() == "production":
            admin_exists = db.query(UserAccount.id).filter(
                UserAccount.role == "admin",
                UserAccount.is_active.is_(True),
            ).first()
            if not admin_exists:
                raise HTTPException(
                    status_code=503,
                    detail="Service is not ready",
                )
    except HTTPException:
        raise
    except SQLAlchemyError as error:
        logger.exception("Readiness database check failed")
        raise HTTPException(
            status_code=503,
            detail="Service is not ready",
        ) from error
    return {"status": "ready", "database": "ok"}


@app.get(
    "/metrics",
    dependencies=[Depends(require_roles("admin"))],
)
def metrics(db: Session = Depends(get_db)):
    lines = []
    for metric, model in (
        ("ai_outbound_calls", Call),
        ("ai_outbound_call_attempts", CallAttempt),
        ("ai_outbound_dispatch_jobs", CallDispatchJob),
    ):
        statuses = db.query(
            model.status,
            func.count(model.id),
        ).group_by(model.status).all()
        for state, count in statuses:
            safe_state = str(state).replace("\\", "\\\\").replace('"', '\\"')
            lines.append(
                f'{metric}{{status="{safe_state}"}} {count}'
            )

    queue_depth = db.query(func.count(CallDispatchJob.id)).filter(
        CallDispatchJob.status == "queued"
    ).scalar()
    pending_webhooks = db.query(func.count(VonageWebhookEvent.event_id)).filter(
        VonageWebhookEvent.processed_at.is_(None)
    ).scalar()
    active_conversations = db.query(
        func.count(ConversationSession.id)
    ).filter(ConversationSession.status == "active").scalar()
    lines.extend([
        f"ai_outbound_dispatch_queue_depth {queue_depth}",
        f"ai_outbound_pending_webhooks {pending_webhooks}",
        f"ai_outbound_active_conversations {active_conversations}",
    ])
    return Response(
        content="\n".join(lines) + "\n",
        media_type="text/plain; version=0.0.4",
    )