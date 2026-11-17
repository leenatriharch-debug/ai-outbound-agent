from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict


class CampaignCreate(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    description: str | None = None
    max_attempts: int = Field(default=3, ge=1, le=10)
    scheduled_at: datetime | None = None


class CampaignUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=150)
    description: str | None = None
    max_attempts: int | None = Field(default=None, ge=1, le=10)
    scheduled_at: datetime | None = None


class CampaignResponse(BaseModel):
    id: int
    name: str
    description: str | None
    status: str
    scheduled_at: datetime | None
    total_contacts: int
    completed_calls: int
    failed_calls: int
    max_attempts: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)