import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.connection import Base
from app.models.call_attempt import CallAttempt
from app.models.campaign import Campaign
from app.models.campaign_contact import CampaignContact
from app.models.customer import Customer
from app.services.call_lifecycle_service import CallLifecycleService
from app.services.call_orchestrator import CallOrchestrator
from app.services.call_queue_service import CallQueueService


class CallLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool
        )
        cls.session_factory = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=cls.engine
        )

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        Base.metadata.create_all(bind=self.engine)
        self.db = self.session_factory()

        self.campaign = Campaign(
            name="Lifecycle test",
            status="active",
            max_attempts=3
        )
        customer = Customer(
            full_name="Test Customer",
            phone_number="+14155550123",
            is_active=True
        )
        self.db.add_all([self.campaign, customer])
        self.db.flush()

        self.contact = CampaignContact(
            campaign_id=self.campaign.id,
            customer_id=customer.id,
            status="calling",
            attempts=1
        )
        self.db.add(self.contact)
        self.db.flush()

        self.attempt = CallAttempt(
            campaign_contact_id=self.contact.id,
            attempt_number=1,
            status="queued",
            started_at=datetime.now(timezone.utc) - timedelta(seconds=5)
        )
        self.db.add(self.attempt)
        self.db.commit()
        self.db.refresh(self.campaign)
        self.db.refresh(self.contact)
        self.db.refresh(self.attempt)

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)

    @staticmethod
    def as_utc(value):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def test_successful_lifecycle_updates_contact_and_counts_once(self):
        for status in ("initiated", "ringing", "answered", "completed"):
            CallLifecycleService.update_status(
                self.db,
                self.attempt.id,
                status
            )

        CallLifecycleService.update_status(
            self.db,
            self.attempt.id,
            "completed"
        )

        self.db.refresh(self.contact)
        self.db.refresh(self.attempt)
        self.db.refresh(self.campaign)

        self.assertEqual(self.attempt.status, "completed")
        self.assertIsNotNone(self.attempt.answered_at)
        self.assertGreaterEqual(self.attempt.duration_seconds, 0)
        self.assertEqual(self.contact.status, "completed")
        self.assertEqual(self.campaign.completed_calls, 1)

    def test_busy_schedules_bounded_retry_without_counting_failure(self):
        CallLifecycleService.update_status(
            self.db,
            self.attempt.id,
            "initiated"
        )
        before = datetime.now(timezone.utc)
        with patch(
            "app.services.call_lifecycle_service.random.uniform",
            return_value=1.0
        ):
            CallLifecycleService.update_status(
                self.db,
                self.attempt.id,
                "busy"
            )

        self.db.refresh(self.contact)
        self.db.refresh(self.campaign)
        retry_at = self.as_utc(self.contact.next_attempt_at)

        self.assertEqual(self.contact.status, "pending")
        self.assertGreaterEqual(retry_at, before + timedelta(seconds=59))
        self.assertLessEqual(retry_at, before + timedelta(seconds=61))
        self.assertEqual(self.campaign.failed_calls, 0)

    def test_failed_attempt_retries_only_when_explicitly_retryable(self):
        CallLifecycleService.update_status(
            self.db,
            self.attempt.id,
            "failed",
            failure_reason="Temporary provider error",
            retryable=True
        )

        self.db.refresh(self.contact)
        self.assertEqual(self.contact.status, "pending")
        self.assertIsNotNone(self.contact.next_attempt_at)

    def test_exhausted_retry_marks_contact_failed_once(self):
        self.campaign.max_attempts = 1
        self.db.commit()
        CallLifecycleService.update_status(
            self.db,
            self.attempt.id,
            "initiated"
        )
        CallLifecycleService.update_status(
            self.db,
            self.attempt.id,
            "no_answer"
        )
        CallLifecycleService.update_status(
            self.db,
            self.attempt.id,
            "no_answer"
        )

        self.db.refresh(self.contact)
        self.db.refresh(self.campaign)
        self.assertEqual(self.contact.status, "failed")
        self.assertIsNone(self.contact.next_attempt_at)
        self.assertEqual(self.campaign.failed_calls, 1)

    def test_invalid_active_transition_is_rejected(self):
        for status in ("answered", "completed"):
            with self.subTest(status=status):
                with self.assertRaisesRegex(
                    ValueError,
                    "Invalid call status transition"
                ):
                    CallLifecycleService.update_status(
                        self.db,
                        self.attempt.id,
                        status
                    )

        self.db.refresh(self.attempt)
        self.assertEqual(self.attempt.status, "queued")

    def test_scheduled_campaign_is_not_queued_before_its_start_time(self):
        customer = self.db.query(Customer).first()
        scheduled_campaign = Campaign(
            name="Future campaign",
            status="active",
            max_attempts=3,
            scheduled_at=datetime.now(timezone.utc) + timedelta(days=1)
        )
        self.db.add(scheduled_campaign)
        self.db.flush()
        scheduled_contact = CampaignContact(
            campaign_id=scheduled_campaign.id,
            customer_id=customer.id,
            status="pending",
            attempts=0
        )
        self.db.add(scheduled_contact)
        self.db.commit()

        self.assertEqual(CallQueueService.get_pending_contacts(self.db), [])
        self.assertIsNone(CallOrchestrator.prepare_next_call(self.db))

        scheduled_campaign.scheduled_at = (
            datetime.now(timezone.utc) - timedelta(minutes=1)
        )
        self.db.commit()
        pending = CallQueueService.get_pending_contacts(self.db)
        self.assertEqual([contact.id for contact in pending], [scheduled_contact.id])

        prepared = CallOrchestrator.prepare_next_call(self.db)
        self.assertEqual(
            prepared["campaign_contact_id"],
            scheduled_contact.id
        )
        self.assertEqual(prepared["attempt_number"], 1)

    def test_inactive_customer_is_finalized_and_counted_as_failed(self):
        inactive_customer = Customer(
            phone_number="+14155550124",
            is_active=False
        )
        self.db.add(inactive_customer)
        self.db.flush()
        contact = CampaignContact(
            campaign_id=self.campaign.id,
            customer_id=inactive_customer.id,
            status="pending",
            attempts=0
        )
        self.db.add(contact)
        self.db.commit()

        self.assertIsNone(CallOrchestrator.prepare_next_call(self.db))

        self.db.refresh(contact)
        self.db.refresh(self.campaign)
        self.assertEqual(contact.status, "failed")
        self.assertIsNone(contact.next_attempt_at)
        self.assertEqual(self.campaign.failed_calls, 1)


if __name__ == "__main__":
    unittest.main()
