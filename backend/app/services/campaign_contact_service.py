from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.customer import Customer
from app.models.campaign_contact import CampaignContact


class CampaignContactService:

    @staticmethod
    def add_customers(db: Session, campaign_id: int, customer_ids: list[int]):
        campaign = db.query(Campaign).filter(
            Campaign.id == campaign_id
        ).first()

        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")

        if campaign.status != "draft":
            raise HTTPException(
                status_code=400,
                detail="Customers can only be added to draft campaigns"
            )

        if not customer_ids:
            raise HTTPException(
                status_code=400,
                detail="At least one customer ID is required"
            )

        if len(customer_ids) != len(set(customer_ids)):
            raise HTTPException(
                status_code=400,
                detail="Duplicate customer IDs are not allowed"
            )

        customers = db.query(Customer).filter(
            Customer.id.in_(customer_ids),
            Customer.is_active.is_(True)
        ).all()

        if len(customers) != len(customer_ids):
            raise HTTPException(
                status_code=400,
                detail="One or more customers do not exist or are inactive"
            )

        existing_ids = {
            row[0] for row in db.query(CampaignContact.customer_id).filter(
                CampaignContact.campaign_id == campaign_id,
                CampaignContact.customer_id.in_(customer_ids)
            ).all()
        }

        new_contacts = [
            CampaignContact(
                campaign_id=campaign_id,
                customer_id=customer_id,
                status="pending"
            )
            for customer_id in customer_ids
            if customer_id not in existing_ids
        ]

        if not new_contacts:
            raise HTTPException(
                status_code=409,
                detail="All selected customers are already in this campaign"
            )

        try:
            db.add_all(new_contacts)
            campaign.total_contacts += len(new_contacts)
            db.commit()

            for contact in new_contacts:
                db.refresh(contact)

            return new_contacts

        except Exception:
            db.rollback()
            raise

    @staticmethod
    def get_campaign_contacts(db: Session, campaign_id: int):
        campaign = db.query(Campaign).filter(
            Campaign.id == campaign_id
        ).first()

        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")

        return db.query(CampaignContact).filter(
            CampaignContact.campaign_id == campaign_id
        ).order_by(CampaignContact.id.desc()).all()