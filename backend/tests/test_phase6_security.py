import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config.settings import settings
from app.database.connection import Base, get_db
from app.main import app
from app.middleware import SlidingWindowLimiter
from app.models.call_attempt import CallAttempt
from app.models.campaign import Campaign
from app.models.campaign_contact import CampaignContact
from app.models.conversation_attempt_link import ConversationAttemptLink
from app.models.conversation_message import ConversationMessage
from app.models.conversation_session import ConversationSession
from app.models.customer import Customer
from app.models.user_account import UserAccount
from app.observability import redact_log_value
from app.services.auth_service import hash_password
from app.services.conversation_maintenance_service import (
    ConversationMaintenanceService,
)
from app.services.auth_service import validate_production_settings
from app.worker import OutboundCallWorker


class Phase6ApiSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        cls.session_factory = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=cls.engine,
        )

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        Base.metadata.create_all(bind=self.engine)
        self.db = self.session_factory()
        self.client = TestClient(app)
        self.previous_app_env = settings.APP_ENV
        self.previous_auth_secret = settings.AUTH_JWT_SECRET
        settings.APP_ENV = "production"
        settings.AUTH_JWT_SECRET = SecretStr(
            "phase6-test-auth-secret-at-least-32-bytes"
        )
        self.admin = UserAccount(
            username="phase6-admin",
            password_hash=hash_password("correct horse battery staple"),
            role="admin",
            is_active=True,
        )
        self.db.add(self.admin)
        self.db.commit()
        self.db.refresh(self.admin)

        def override_get_db():
            db = self.session_factory()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        settings.APP_ENV = self.previous_app_env
        settings.AUTH_JWT_SECRET = self.previous_auth_secret
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)

    def _login(self, username, password):
        response = self.client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    def test_api_requires_token_and_enforces_operator_role(self):
        self.assertEqual(
            self.client.get("/api/customers/").status_code,
            401,
        )
        admin_headers = self._login(
            "phase6-admin",
            "correct horse battery staple",
        )
        created = self.client.post(
            "/api/auth/users",
            json={
                "username": "phase6-operator",
                "password": "operator password with length",
                "role": "operator",
            },
            headers=admin_headers,
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.assertNotIn("password_hash", created.json())
        operator_id = created.json()["id"]

        operator_headers = self._login(
            "phase6-operator",
            "operator password with length",
        )
        self.assertEqual(
            self.client.get(
                "/api/customers/",
                headers=operator_headers,
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                "/api/ivr/menus/",
                headers=operator_headers,
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                "/api/auth/users",
                headers=operator_headers,
            ).status_code,
            403,
        )
        disabled = self.client.patch(
            f"/api/auth/users/{self.admin.id}",
            json={"is_active": False},
            headers=admin_headers,
        )
        self.assertEqual(disabled.status_code, 409)

        deactivated = self.client.patch(
            f"/api/auth/users/{operator_id}",
            json={"is_active": False},
            headers=admin_headers,
        )
        self.assertEqual(deactivated.status_code, 200, deactivated.text)
        self.assertEqual(
            self.client.get(
                "/api/customers/",
                headers=operator_headers,
            ).status_code,
            401,
        )

    def test_request_id_readiness_and_redacted_logging_helpers(self):
        response = self.client.get(
            "/health",
            headers={"X-Request-ID": "phase6-test-id"},
        )
        self.assertEqual(response.headers["X-Request-ID"], "phase6-test-id")
        self.assertEqual(self.client.get("/ready").json()["status"], "ready")
        self.assertEqual(
            redact_log_value(
                "Authorization: Bearer secret-token to +14155550123"
            ),
            "Authorization: Bearer [REDACTED] to [REDACTED_PHONE]",
        )
        admin_headers = self._login(
            "phase6-admin",
            "correct horse battery staple",
        )
        metrics_response = self.client.get("/metrics", headers=admin_headers)
        self.assertEqual(metrics_response.status_code, 200)
        self.assertIn("ai_outbound_dispatch_queue_depth", metrics_response.text)

    def test_production_settings_fail_closed_when_secrets_are_missing(self):
        with (
            patch.object(settings, "APP_ENV", "production"),
            patch.object(settings, "AUTH_JWT_SECRET", SecretStr("short")),
            self.assertRaises(RuntimeError) as raised,
        ):
            validate_production_settings()
        self.assertIn("AUTH_JWT_SECRET", str(raised.exception))

    def test_rate_limiter_rejects_requests_after_configured_window_limit(self):
        limiter = SlidingWindowLimiter(window_seconds=60)
        self.assertEqual(limiter.allow("client", "login", 2), (True, 0))
        self.assertEqual(limiter.allow("client", "login", 2), (True, 0))
        allowed, retry_after = limiter.allow("client", "login", 2)
        self.assertFalse(allowed)
        self.assertGreaterEqual(retry_after, 1)


class Phase6ConversationMaintenanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        cls.session_factory = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=cls.engine,
        )

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        Base.metadata.create_all(bind=self.engine)
        self.db = self.session_factory()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)

    def test_stale_active_conversation_is_marked_failed(self):
        session = ConversationSession(
            session_id="stale-session",
            status="active",
            started_at=datetime.now(timezone.utc) - timedelta(minutes=151),
        )
        self.db.add(session)
        self.db.commit()

        recovered = ConversationMaintenanceService.recover_stale_sessions(
            self.db,
            stale_minutes=150,
        )

        self.assertEqual(recovered, 1)
        self.db.refresh(session)
        self.assertEqual(session.status, "failed")
        self.assertIsNotNone(session.ended_at)

    def test_worker_runs_conversation_maintenance(self):
        session = ConversationSession(
            session_id="worker-stale-session",
            status="active",
            started_at=datetime.now(timezone.utc) - timedelta(minutes=151),
        )
        self.db.add(session)
        self.db.commit()
        worker = OutboundCallWorker(
            session_factory=self.session_factory,
            poll_interval=0,
            maintenance_interval=60,
        )

        self.assertTrue(worker.run_once())

        self.db.refresh(session)
        self.assertEqual(session.status, "failed")

    def test_expired_transcript_and_attempt_link_are_deleted(self):
        campaign = Campaign(name="Retention test", status="completed")
        customer = Customer(
            full_name="Retention customer",
            phone_number="+14155550123",
            is_active=True,
        )
        self.db.add_all([campaign, customer])
        self.db.flush()
        contact = CampaignContact(
            campaign_id=campaign.id,
            customer_id=customer.id,
            status="completed",
        )
        self.db.add(contact)
        self.db.flush()
        attempt = CallAttempt(
            campaign_contact_id=contact.id,
            attempt_number=1,
            status="completed",
        )
        self.db.add(attempt)
        self.db.flush()
        old_date = datetime.now(timezone.utc) - timedelta(days=91)
        session = ConversationSession(
            session_id="expired-session",
            customer_id=customer.id,
            status="completed",
            started_at=old_date,
            ended_at=old_date,
        )
        self.db.add(session)
        self.db.flush()
        self.db.add_all([
            ConversationAttemptLink(
                conversation_session_id=session.id,
                call_attempt_id=attempt.id,
            ),
            ConversationMessage(
                session_id=session.id,
                role="user",
                message="private transcript",
            ),
        ])
        self.db.commit()
        session_id = session.id
        attempt_id = attempt.id

        deleted = ConversationMaintenanceService.purge_expired_transcripts(
            self.db,
            retention_days=90,
        )

        self.assertEqual(deleted, 1)
        self.assertIsNone(self.db.get(ConversationSession, session_id))
        self.assertEqual(
            self.db.query(ConversationMessage).filter_by(
                session_id=session_id
            ).count(),
            0,
        )
        self.assertEqual(
            self.db.query(ConversationAttemptLink).filter_by(
                call_attempt_id=attempt_id
            ).count(),
            0,
        )
        self.assertIsNotNone(self.db.get(CallAttempt, attempt_id))


if __name__ == "__main__":
    unittest.main()
