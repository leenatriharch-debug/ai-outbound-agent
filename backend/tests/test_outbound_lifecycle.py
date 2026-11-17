import hashlib
import json
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import jwt
from fastapi.testclient import TestClient
from requests import Response
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config.settings import settings
from app.database.connection import Base, get_db
from app.main import app
from app.models.call import Call
from app.models.call_attempt import CallAttempt
from app.models.call_dispatch_job import CallDispatchJob
from app.models.campaign import Campaign
from app.models.campaign_contact import CampaignContact
from app.models.customer import Customer
from app.models.vonage_webhook_event import VonageWebhookEvent
from app.services.call_dispatch_service import CallDispatchService
from app.services.call_lifecycle_service import CallLifecycleService
from app.services.call_orchestrator import CallOrchestrator
from app.services.provider_errors import ProviderCallRejected
from app.services.telephony_provider import CallResult
from app.services.vonage_webhook_security import (
    build_callback_urls,
    build_media_websocket_url,
    verify_dispatch_callback,
)
from app.services.vonage_service import VonageService
from app.worker import OutboundCallWorker
from vonage_http_client.errors import HttpRequestError


class FakeTelephonyProvider:

    def __init__(self, result=None, error=None):
        self.result = result or CallResult("vonage-call-1", "started")
        self.error = error
        self.requests = []

    def create_call(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return self.result


class OutboundLifecycleTests(unittest.TestCase):
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
        self.previous_public_url = settings.PUBLIC_BASE_URL
        self.previous_api_key = settings.VONAGE_API_KEY
        self.previous_signature_secret = settings.VONAGE_SIGNATURE_SECRET
        settings.PUBLIC_BASE_URL = "https://voice.example.test"
        settings.VONAGE_API_KEY = "test-api-key"
        settings.VONAGE_SIGNATURE_SECRET = "test-signature-secret-key-32-bytes"

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
        settings.PUBLIC_BASE_URL = self.previous_public_url
        settings.VONAGE_API_KEY = self.previous_api_key
        settings.VONAGE_SIGNATURE_SECRET = self.previous_signature_secret
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)

    def create_campaign_attempt(self, max_attempts=2):
        campaign = Campaign(
            name="Outbound integration",
            status="active",
            max_attempts=max_attempts,
            total_contacts=1,
        )
        customer = Customer(
            full_name="Test Customer",
            phone_number="+14155550123",
            is_active=True,
        )
        self.db.add_all([campaign, customer])
        self.db.flush()
        contact = CampaignContact(
            campaign_id=campaign.id,
            customer_id=customer.id,
            status="pending",
        )
        self.db.add(contact)
        self.db.commit()
        prepared = CallOrchestrator.prepare_next_call(self.db)
        attempt = self.db.query(CallAttempt).filter(
            CallAttempt.id == prepared["attempt_id"]
        ).one()
        job = self.db.query(CallDispatchJob).filter(
            CallDispatchJob.call_attempt_id == attempt.id
        ).one()
        return campaign, contact, attempt, job

    @staticmethod
    def _vonage_token(body=b"", event_id=None):
        claims = {
            "iat": int(time.time()),
            "jti": event_id or uuid.uuid4().hex,
            "iss": "Vonage",
            "api_key": settings.VONAGE_API_KEY,
            "application_id": "test-application",
        }
        if body:
            claims["payload_hash"] = hashlib.sha256(body).hexdigest()
        return jwt.encode(
            claims,
            settings.VONAGE_SIGNATURE_SECRET,
            algorithm="HS256",
        )

    def _send_event(self, job_id, event, event_id=None):
        body = json.dumps(event, separators=(",", ":")).encode("utf-8")
        _, event_url = build_callback_urls(
            settings.PUBLIC_BASE_URL,
            job_id,
            settings.VONAGE_SIGNATURE_SECRET,
        )
        parsed_url = urlsplit(event_url)
        token = self._vonage_token(body, event_id)
        return self.client.post(
            f"{parsed_url.path}?{parsed_url.query}",
            content=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )

    def test_worker_dispatches_campaign_attempt_once_with_signed_callbacks(self):
        campaign, contact, attempt, job = self.create_campaign_attempt()
        provider = FakeTelephonyProvider()
        registry = SimpleNamespace(
            get_telephony_provider=lambda: provider
        )
        worker = OutboundCallWorker(
            session_factory=self.session_factory,
            poll_interval=0,
        )

        with patch(
            "app.services.call_dispatch_service.get_provider_registry",
            return_value=registry,
        ):
            self.assertTrue(worker.run_once())
            self.assertFalse(worker.run_once())

        self.db.expire_all()
        attempt = self.db.query(CallAttempt).filter(
            CallAttempt.id == attempt.id
        ).one()
        job = self.db.query(CallDispatchJob).filter(
            CallDispatchJob.id == job.id
        ).one()
        contact = self.db.query(CampaignContact).filter(
            CampaignContact.id == contact.id
        ).one()

        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(provider.requests[0].phone_number, "+14155550123")
        self.assertEqual(attempt.status, "initiated")
        self.assertEqual(attempt.call_uuid, "vonage-call-1")
        self.assertEqual(job.status, "dispatched")
        self.assertEqual(job.provider_call_uuid, attempt.call_uuid)
        self.assertEqual(contact.status, "calling")

        answer_url, event_url = build_callback_urls(
            settings.PUBLIC_BASE_URL,
            job.id,
            settings.VONAGE_SIGNATURE_SECRET,
        )
        self.assertEqual(provider.requests[0].answer_url, answer_url)
        self.assertEqual(provider.requests[0].event_url, event_url)

        answer_parsed = urlsplit(answer_url)
        answer_token = self._vonage_token()
        answer_response = self.client.get(
            f"{answer_parsed.path}?{answer_parsed.query}",
            headers={"Authorization": f"Bearer {answer_token}"},
        )
        self.assertEqual(answer_response.status_code, 200)
        ncco = answer_response.json()
        self.assertEqual(ncco[0]["action"], "connect")
        self.assertEqual(ncco[0]["eventType"], "synchronous")
        endpoint = ncco[0]["endpoint"][0]
        self.assertEqual(endpoint["type"], "websocket")
        self.assertTrue(endpoint["uri"].startswith("wss://"))
        self.assertEqual(endpoint["content-type"], "audio/l16;rate=16000")
        self.assertEqual(endpoint["authorization"]["type"], "vonage")

    def test_signed_webhooks_bind_uuid_and_are_idempotent(self):
        campaign, contact, attempt, job = self.create_campaign_attempt()

        answered = self._send_event(
            job.id,
            {"uuid": "callback-first-uuid", "status": "answered"},
            event_id="event-answered",
        )
        self.assertEqual(answered.status_code, 200, answered.text)
        self.assertEqual(answered.json()["status"], "updated")

        duplicate = self._send_event(
            job.id,
            {"uuid": "callback-first-uuid", "status": "answered"},
            event_id="event-answered",
        )
        self.assertEqual(duplicate.status_code, 200, duplicate.text)
        self.assertEqual(duplicate.json()["status"], "duplicate")

        completed = self._send_event(
            job.id,
            {"uuid": "callback-first-uuid", "status": "completed"},
            event_id="event-completed",
        )
        self.assertEqual(completed.status_code, 200, completed.text)

        self.db.expire_all()
        attempt = self.db.query(CallAttempt).filter(
            CallAttempt.id == attempt.id
        ).one()
        contact = self.db.query(CampaignContact).filter(
            CampaignContact.id == contact.id
        ).one()
        campaign = self.db.query(Campaign).filter(
            Campaign.id == campaign.id
        ).one()
        job = self.db.query(CallDispatchJob).filter(
            CallDispatchJob.id == job.id
        ).one()

        self.assertEqual(attempt.status, "completed")
        self.assertEqual(attempt.call_uuid, "callback-first-uuid")
        self.assertEqual(contact.status, "completed")
        self.assertEqual(campaign.completed_calls, 1)
        self.assertEqual(campaign.status, "completed")
        self.assertEqual(job.status, "dispatched")
        self.assertEqual(
            self.db.query(VonageWebhookEvent).count(),
            2,
        )

        duplicate_completion = self._send_event(
            job.id,
            {"uuid": "callback-first-uuid", "status": "completed"},
            event_id="event-completed",
        )
        self.assertEqual(duplicate_completion.json()["status"], "duplicate")
        self.db.refresh(campaign)
        self.assertEqual(campaign.completed_calls, 1)

    def test_invalid_or_unsigned_webhook_is_rejected(self):
        response = self.client.post(
            "/api/voice/events",
            json={"uuid": "spoofed", "status": "completed"},
        )
        self.assertEqual(response.status_code, 401)

        body = b'{"uuid":"spoofed","status":"completed"}'
        token = self._vonage_token(body)
        tampered_response = self.client.post(
            "/api/voice/events",
            content=b'{"uuid":"spoofed","status":"busy"}',
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(tampered_response.status_code, 401)

    def test_direct_outbound_api_enqueues_legacy_call(self):
        response = self.client.post(
            "/api/calls/outbound",
            json={"phone_number": "+14155550123"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        call_id = response.json()["id"]
        call = self.db.query(Call).filter(Call.id == call_id).one()
        job = self.db.query(CallDispatchJob).filter(
            CallDispatchJob.call_id == call_id
        ).one()
        self.assertEqual(call.status, "pending")
        self.assertEqual(job.status, "queued")

    def test_direct_call_dispatch_and_webhook_use_shared_lifecycle(self):
        response = self.client.post(
            "/api/calls/outbound",
            json={"phone_number": "+14155550123"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        call_id = response.json()["id"]
        job = self.db.query(CallDispatchJob).filter(
            CallDispatchJob.call_id == call_id
        ).one()
        provider = FakeTelephonyProvider(
            result=CallResult("direct-call-uuid", "started")
        )

        self.assertTrue(
            CallDispatchService.dispatch_next(
                self.db,
                provider=provider,
                base_url=settings.PUBLIC_BASE_URL,
                signature_secret=settings.VONAGE_SIGNATURE_SECRET,
            )
        )
        event_response = self._send_event(
            job.id,
            {"uuid": "direct-call-uuid", "status": "completed"},
            event_id="direct-call-completed",
        )
        self.assertEqual(event_response.status_code, 200)

        self.db.expire_all()
        call = self.db.query(Call).filter(Call.id == call_id).one()
        self.assertEqual(call.call_uuid, "direct-call-uuid")
        self.assertEqual(call.status, "completed")

    def test_definitive_dispatch_rejection_retries_only_when_allowed(self):
        campaign, contact, attempt, job = self.create_campaign_attempt()
        provider = FakeTelephonyProvider(
            error=ProviderCallRejected("Rate limited", retryable=True)
        )

        self.assertTrue(
            CallDispatchService.dispatch_next(
                self.db,
                provider=provider,
                base_url=settings.PUBLIC_BASE_URL,
                signature_secret=settings.VONAGE_SIGNATURE_SECRET,
            )
        )

        self.db.expire_all()
        attempt = self.db.query(CallAttempt).filter(
            CallAttempt.id == attempt.id
        ).one()
        contact = self.db.query(CampaignContact).filter(
            CampaignContact.id == contact.id
        ).one()
        self.assertEqual(attempt.status, "failed")
        self.assertEqual(contact.status, "pending")
        self.assertIsNotNone(contact.next_attempt_at)

        contact.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.db.commit()
        retry = CallOrchestrator.prepare_next_call(self.db)
        self.assertEqual(retry["attempt_number"], 2)

    def test_non_retryable_dispatch_failure_completes_campaign(self):
        campaign, contact, attempt, job = self.create_campaign_attempt(
            max_attempts=1
        )
        provider = FakeTelephonyProvider(
            error=ProviderCallRejected("Invalid destination", retryable=False)
        )

        self.assertTrue(
            CallDispatchService.dispatch_next(
                self.db,
                provider=provider,
                base_url=settings.PUBLIC_BASE_URL,
                signature_secret=settings.VONAGE_SIGNATURE_SECRET,
            )
        )

        self.db.expire_all()
        attempt = self.db.query(CallAttempt).filter(
            CallAttempt.id == attempt.id
        ).one()
        contact = self.db.query(CampaignContact).filter(
            CampaignContact.id == contact.id
        ).one()
        campaign = self.db.query(Campaign).filter(
            Campaign.id == campaign.id
        ).one()

        self.assertEqual(attempt.status, "failed")
        self.assertEqual(contact.status, "failed")
        self.assertEqual(campaign.failed_calls, 1)
        self.assertEqual(campaign.status, "completed")

    def test_expired_worker_lease_is_not_redispatched(self):
        campaign, contact, attempt, job = self.create_campaign_attempt()
        attempt.status = "dispatching"
        job.status = "dispatching"
        job.claimed_at = datetime.now(timezone.utc) - timedelta(minutes=3)
        self.db.commit()

        self.assertEqual(
            CallDispatchService.recover_stale_dispatches(self.db),
            1,
        )

        self.db.refresh(attempt)
        self.db.refresh(job)
        self.db.refresh(contact)
        self.assertEqual(attempt.status, "dispatch_unknown")
        self.assertEqual(job.status, "unknown")
        self.assertEqual(contact.status, "calling")

    def test_ambiguous_dispatch_is_not_retried(self):
        campaign, contact, attempt, job = self.create_campaign_attempt()
        provider = FakeTelephonyProvider(error=TimeoutError("request timed out"))

        with self.assertLogs(
            "app.services.call_dispatch_service",
            level="WARNING",
        ):
            self.assertTrue(
                CallDispatchService.dispatch_next(
                    self.db,
                    provider=provider,
                    base_url=settings.PUBLIC_BASE_URL,
                    signature_secret=settings.VONAGE_SIGNATURE_SECRET,
                )
            )
        self.assertFalse(
            CallDispatchService.dispatch_next(
                self.db,
                provider=provider,
                base_url=settings.PUBLIC_BASE_URL,
                signature_secret=settings.VONAGE_SIGNATURE_SECRET,
            )
        )

        self.db.expire_all()
        attempt = self.db.query(CallAttempt).filter(
            CallAttempt.id == attempt.id
        ).one()
        job = self.db.query(CallDispatchJob).filter(
            CallDispatchJob.id == job.id
        ).one()
        contact = self.db.query(CampaignContact).filter(
            CampaignContact.id == contact.id
        ).one()
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(attempt.status, "dispatch_unknown")
        self.assertEqual(job.status, "unknown")
        self.assertEqual(contact.status, "calling")

    def test_vonage_rate_limit_is_classified_as_retryable_rejection(self):
        response = Response()
        response.status_code = 429
        response.url = "https://api.nexmo.com/v1/calls"
        error = HttpRequestError(response)

        class FailingVoice:
            def create_call(self, payload):
                raise error

        voice = FailingVoice()
        service = VonageService(client=SimpleNamespace(voice=voice))

        with patch.object(settings, "VONAGE_NUMBER", "+14155550124"):
            with self.assertRaises(ProviderCallRejected) as raised:
                service.make_call(
                    "+14155550123",
                    "https://voice.example.test/answer",
                    "https://voice.example.test/events",
                )

        self.assertTrue(raised.exception.retryable)

    def test_outbound_answer_uses_signed_websocket_audio_endpoint(self):
        _, _, attempt, job = self.create_campaign_attempt()
        attempt.status = "initiated"
        attempt.call_uuid = "live-call-uuid"
        job.status = "dispatched"
        job.provider_call_uuid = "live-call-uuid"
        self.db.commit()
        answer_url, _ = build_callback_urls(
            settings.PUBLIC_BASE_URL,
            job.id,
            settings.VONAGE_SIGNATURE_SECRET,
        )
        parsed_answer = urlsplit(answer_url)
        response = self.client.get(
            f"{parsed_answer.path}?{parsed_answer.query}",
            headers={"Authorization": f"Bearer {self._vonage_token()}"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        ncco = response.json()
        endpoint = ncco[0]["endpoint"][0]
        self.assertEqual(ncco[0]["action"], "connect")
        self.assertEqual(ncco[0]["eventType"], "synchronous")
        self.assertEqual(endpoint["type"], "websocket")
        self.assertEqual(endpoint["content-type"], "audio/l16;rate=16000")

        media_url = build_media_websocket_url(
            settings.PUBLIC_BASE_URL,
            job.id,
            settings.VONAGE_SIGNATURE_SECRET,
        )
        parsed_media = urlsplit(media_url)
        media_query = parse_qs(parsed_media.query)
        self.assertEqual(parsed_media.scheme, "wss")
        self.assertEqual(parsed_media.path, "/api/voice/media")
        self.assertTrue(verify_dispatch_callback(
            job.id,
            media_query["dispatch_sig"][0],
            "media",
        ))

    def test_failed_websocket_connection_returns_fallback_ncco(self):
        _, _, attempt, job = self.create_campaign_attempt()
        attempt.status = "initiated"
        attempt.call_uuid = "failed-media-call"
        job.status = "dispatched"
        job.provider_call_uuid = "failed-media-call"
        self.db.commit()
        _, event_url = build_callback_urls(
            settings.PUBLIC_BASE_URL,
            job.id,
            settings.VONAGE_SIGNATURE_SECRET,
        )
        parsed_url = urlsplit(event_url)
        body = json.dumps({
            "uuid": "failed-media-call",
            "status": "unanswered",
        }).encode("utf-8")
        response = self.client.post(
            f"{parsed_url.path}?{parsed_url.query}",
            content=body,
            headers={
                "Authorization": f"Bearer {self._vonage_token(body)}",
                "Content-Type": "application/json",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()[0]["action"], "talk")
        self.assertIn("unable to continue", response.json()[0]["text"])
        self.db.refresh(attempt)
        self.assertEqual(attempt.status, "no_answer")

    def test_websocket_endpoint_requires_both_signatures(self):
        _, _, attempt, job = self.create_campaign_attempt()
        attempt.status = "initiated"
        attempt.call_uuid = "live-call-uuid"
        job.status = "dispatched"
        job.provider_call_uuid = "live-call-uuid"
        self.db.commit()
        media_url = build_media_websocket_url(
            settings.PUBLIC_BASE_URL,
            job.id,
            settings.VONAGE_SIGNATURE_SECRET,
        )
        parsed = urlsplit(media_url)

        class FakeHandler:
            async def run(self, websocket):
                await websocket.accept()
                await websocket.receive_text()
                await websocket.close()

        fake_handler = FakeHandler()
        registry = SimpleNamespace(
            get_telephony_provider=lambda: FakeTelephonyProvider()
        )
        with (
            patch(
                "app.routes.voice_routes.LiveCallConversationService",
                return_value=SimpleNamespace(),
            ),
            patch(
                "app.routes.voice_routes.VonageMediaStreamHandler",
                return_value=fake_handler,
            ),
            patch(
                "app.routes.voice_routes.get_provider_registry",
                return_value=registry,
            ),
            patch(
                "app.routes.voice_routes.SessionLocal",
                self.session_factory,
            ),
        ):
            with self.client.websocket_connect(
                f"{parsed.path}?{parsed.query}",
                headers={"Authorization": f"Bearer {self._vonage_token()}"},
            ) as websocket:
                websocket.send_text("Vonage test handshake")


if __name__ == "__main__":
    unittest.main()
