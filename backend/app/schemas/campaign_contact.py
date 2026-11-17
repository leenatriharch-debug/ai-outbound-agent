from datetime import datetime
from pydantic import BaseModel, ConfigDict


class CampaignContactCreate(BaseModel):
    customer_ids: list[int]


class CampaignContactResponse(BaseModel):
    id: int
    campaign_id: int
    customer_id: int
    status: str
    attempts: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)