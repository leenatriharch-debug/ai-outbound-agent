from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.schemas.campaign import (
    CampaignCreate,
    CampaignUpdate,
    CampaignResponse
)
from app.controllers.campaign_controller import CampaignController
from app.services.auth_service import require_roles

router = APIRouter(
    prefix="/api/campaigns",
    tags=["Campaigns"],
    dependencies=[Depends(require_roles("admin", "operator"))],
)


@router.post("/", response_model=CampaignResponse, status_code=201)
def create_campaign(data: CampaignCreate, db: Session = Depends(get_db)):
    return CampaignController.create_campaign(db, data)


@router.get("/", response_model=list[CampaignResponse])
def get_campaigns(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db)
):
    return CampaignController.get_campaigns(db, skip, limit)


@router.get("/{campaign_id}", response_model=CampaignResponse)
def get_campaign(campaign_id: int, db: Session = Depends(get_db)):
    return CampaignController.get_campaign(db, campaign_id)


@router.put("/{campaign_id}", response_model=CampaignResponse)
def update_campaign(
    campaign_id: int,
    data: CampaignUpdate,
    db: Session = Depends(get_db)
):
    return CampaignController.update_campaign(db, campaign_id, data)


@router.patch("/{campaign_id}/status", response_model=CampaignResponse)
def update_campaign_status(
    campaign_id: int,
    status: str,
    db: Session = Depends(get_db)
):
    return CampaignController.update_campaign_status(db, campaign_id, status)