from sqlalchemy.orm import Session
from app.schemas.campaign import CampaignCreate, CampaignUpdate
from app.services.campaign_service import CampaignService


class CampaignController:

    @staticmethod
    def create_campaign(db: Session, data: CampaignCreate):
        return CampaignService.create_campaign(db, data)

    @staticmethod
    def get_campaigns(db: Session, skip: int = 0, limit: int = 50):
        return CampaignService.get_campaigns(db, skip, limit)

    @staticmethod
    def get_campaign(db: Session, campaign_id: int):
        return CampaignService.get_campaign(db, campaign_id)

    @staticmethod
    def update_campaign(db: Session, campaign_id: int, data: CampaignUpdate):
        return CampaignService.update_campaign(db, campaign_id, data)

    @staticmethod
    def update_campaign_status(db: Session, campaign_id: int, status: str):
        return CampaignService.update_campaign_status(db, campaign_id, status)