from datetime import datetime
from pydantic import BaseModel, ConfigDict


class ConversationSessionCreate(BaseModel):
    customer_id: int | None = None
    call_id: int | None = None


class ConversationSessionResponse(BaseModel):
    id: int
    session_id: str
    customer_id: int | None
    call_id: int | None
    status: str
    conversation_summary: str | None
    started_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ConversationMessageCreate(BaseModel):
    role: str
    message: str


class ConversationMessageResponse(BaseModel):
    id: int
    session_id: int
    role: str
    message: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)