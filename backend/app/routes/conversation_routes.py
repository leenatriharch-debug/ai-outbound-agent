from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.schemas.conversation import (
    ConversationSessionCreate,
    ConversationSessionResponse,
    ConversationMessageCreate,
    ConversationMessageResponse
)
from app.controllers.conversation_controller import ConversationController
from app.services.agent_orchestrator import AgentOrchestrator
from app.services.provider_registry import (
    get_provider_registry
)
from app.services.provider_errors import ProviderConfigurationError
from app.services.auth_service import require_roles

router = APIRouter(
    prefix="/api/conversations",
    tags=["AI Conversations"],
    dependencies=[Depends(require_roles("admin", "operator"))],
)


@router.post("/sessions", response_model=ConversationSessionResponse)
def create_session(
    data: ConversationSessionCreate,
    db: Session = Depends(get_db)
):
    return ConversationController.create_session(
        db,
        data.customer_id,
        data.call_id
    )


@router.get("/sessions/{session_id}/messages",
            response_model=list[ConversationMessageResponse])
def get_history(
    session_id: int,
    db: Session = Depends(get_db)
):
    try:
        return ConversationController.get_history(db, session_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error))


@router.post("/sessions/{session_id}/messages",
             response_model=ConversationMessageResponse)
def add_message(
    session_id: int,
    data: ConversationMessageCreate,
    db: Session = Depends(get_db)
):
    try:
        return ConversationController.add_message(
            db, session_id, data.role, data.message
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


@router.post("/sessions/{session_id}/chat")
async def chat_with_ai(
    session_id: int,
    data: ConversationMessageCreate,
    db: Session = Depends(get_db)
):
    try:
        provider = get_provider_registry().get_llm_provider()
        orchestrator = AgentOrchestrator(provider)

        return await orchestrator.handle_customer_message(
            db=db,
            session_id=session_id,
            customer_message=data.message,
            company_name="Worldotech",
            purpose="customer outreach"
        )

    except ProviderConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


@router.patch("/sessions/{session_id}/end",
              response_model=ConversationSessionResponse)
def end_session(
    session_id: int,
    db: Session = Depends(get_db)
):
    try:
        return ConversationController.end_session(db, session_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error))