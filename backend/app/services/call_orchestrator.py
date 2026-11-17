from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.campaign_contact import CampaignContact
from app.models.call_attempt import CallAttempt
from app.models.call_dispatch_job import CallDispatchJob
from app.models.customer import Customer
from app.services.call_lifecycle_service import CallLifecycleService
from app.services.call_queue_service import CallQueueService


class CallOrchestrator:

    @staticmethod
    def prepare_next_call(db: Session):
        contact = CallQueueService.get_next_pending_contact_for_update(db)

        if not contact:
            return None

        customer = db.query(Customer).filter(
            Customer.id == contact.customer_id,
            Customer.is_active.is_(True)
        ).first()

        if not customer:
            contact.status = "failed"
            contact.last_error = "Customer not found or inactive"
            contact.next_attempt_at = None
            db.query(Campaign).filter(
                Campaign.id == contact.campaign_id
            ).update(
                {Campaign.failed_calls: Campaign.failed_calls + 1},
                synchronize_session=False
            )
            CallLifecycleService.update_campaign_completion(
                db,
                contact.campaign_id
            )
            db.commit()
            return None

        attempt_number = contact.attempts + 1

        attempt = CallAttempt(
            campaign_contact_id=contact.id,
            attempt_number=attempt_number,
            status="queued"
        )

        try:
            db.add(attempt)
            db.flush()
            db.add(CallDispatchJob(call_attempt_id=attempt.id))
            contact.attempts = attempt_number
            contact.status = "calling"
            db.commit()
            db.refresh(attempt)

            return {
                "attempt_id": attempt.id,
                "campaign_contact_id": contact.id,
                "customer_id": customer.id,
                "phone_number": customer.phone_number,
                "attempt_number": attempt.attempt_number,
                "status": attempt.status
            }

        except Exception:
            db.rollback()
            raise