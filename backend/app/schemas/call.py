from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime


class CallCreate(BaseModel):
    phone_number: str = Field(
        min_length=10,
        max_length=20
    )


class CallResponse(BaseModel):
    id: int
    call_uuid: str | None = None
    phone_number: str
    status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)