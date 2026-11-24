from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.schemas.campaign_contact import (
    CampaignContactCreate,
    CampaignContactResponse
)
from app.services.campaign_contact_service import CampaignContactService
from app.services.auth_service import require_roles

router = APIRouter(
    prefix="/api/campaigns",
    tags=["Campaign Contacts"],
    dependencies=[Depends(require_roles("admin", "operator"))],
)


@router.post("/{campaign_id}/contacts", response_model=list[CampaignContactResponse])
def add_campaign_customers(
    campaign_id: int,
    data: CampaignContactCreate,
    db: Session = Depends(get_db)
):
    return CampaignContactService.add_customers(
        db, campaign_id, data.customer_ids
    )


@router.get("/{campaign_id}/contacts", response_model=list[CampaignContactResponse])
def get_campaign_customers(
    campaign_id: int,
    db: Session = Depends(get_db)
):
    return CampaignContactService.get_campaign_contacts(db, campaign_id)