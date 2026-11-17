from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.schemas.campaign import CampaignCreate, CampaignUpdate
from app.services.call_lifecycle_service import CallLifecycleService


class CampaignService:

    @staticmethod
    def create_campaign(db: Session, data: CampaignCreate):
        campaign = Campaign(
            name=data.name,
            description=data.description,
            max_attempts=data.max_attempts,
            scheduled_at=data.scheduled_at,
            status="draft"
        )

        db.add(campaign)
        db.commit()
        db.refresh(campaign)

        return campaign

    @staticmethod
    def get_campaigns(db: Session, skip: int = 0, limit: int = 50):
        return (
            db.query(Campaign)
            .order_by(Campaign.id.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

    @staticmethod
    def get_campaign(db: Session, campaign_id: int):
        campaign = db.query(Campaign).filter(
            Campaign.id == campaign_id
        ).first()

        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")

        return campaign

    @staticmethod
    def update_campaign(
        db: Session,
        campaign_id: int,
        data: CampaignUpdate
    ):
        campaign = CampaignService.get_campaign(db, campaign_id)

        if campaign.status in ["active", "completed"]:
            raise HTTPException(
                status_code=400,
                detail="This campaign cannot be edited in its current status"
            )

        updates = data.model_dump(exclude_unset=True)

        for key, value in updates.items():
            setattr(campaign, key, value)

        db.commit()
        db.refresh(campaign)

        return campaign

    @staticmethod
    def update_campaign_status(
        db: Session,
        campaign_id: int,
        status: str
    ):
        campaign = CampaignService.get_campaign(db, campaign_id)

        allowed_statuses = ["draft", "active", "paused", "cancelled"]

        if status not in allowed_statuses:
            raise HTTPException(
                status_code=400,
                detail="Invalid campaign status"
            )

        if campaign.status == "completed":
            raise HTTPException(
                status_code=400,
                detail="Completed campaigns cannot be changed"
            )

        campaign.status = status
        if status == "active":
            CallLifecycleService.update_campaign_completion(db, campaign_id)
        db.commit()
        db.refresh(campaign)

        return campaign