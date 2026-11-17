from datetime import datetime, timezone

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.campaign_contact import CampaignContact
from app.models.campaign import Campaign


class CallQueueService:

    @staticmethod
    def _eligible_contacts_query(db: Session, now: datetime):
        return (
            db.query(CampaignContact)
            .join(
                Campaign,
                Campaign.id == CampaignContact.campaign_id
            )
            .filter(
                Campaign.status == "active",
                or_(
                    Campaign.scheduled_at.is_(None),
                    Campaign.scheduled_at <= now
                ),
                CampaignContact.status == "pending",
                CampaignContact.attempts < Campaign.max_attempts,
                or_(
                    CampaignContact.next_attempt_at.is_(None),
                    CampaignContact.next_attempt_at <= now
                )
            )
        )

    @staticmethod
    def get_next_pending_contact_for_update(db: Session):
        now = datetime.now(timezone.utc)
        return CallQueueService._eligible_contacts_query(
            db,
            now
        ).order_by(
            CampaignContact.created_at.asc()
        ).with_for_update(skip_locked=True).first()

    @staticmethod
    def get_pending_contacts(
        db: Session,
        limit: int = 10
    ):
        now = datetime.now(timezone.utc)
        return CallQueueService._eligible_contacts_query(
            db,
            now
        ).order_by(
            CampaignContact.created_at.asc()
        ).limit(limit).all()