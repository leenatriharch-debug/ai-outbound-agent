import time
import unittest
import uuid

from fastapi.testclient import TestClient
import jwt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config.settings import settings
from app.database.connection import Base, get_db
from app.main import app
from app.models import IVRMenu, IVRMenuOption


class IVRApiTests(unittest.TestCase):
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

    def setUp(self):
        Base.metadata.create_all(bind=self.engine)

        def override_get_db():
            db = self.session_factory()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)
        self.previous_public_url = settings.PUBLIC_BASE_URL
        self.previous_vonage_api_key = settings.VONAGE_API_KEY
        self.previous_signature_secret = settings.VONAGE_SIGNATURE_SECRET
        settings.PUBLIC_BASE_URL = "https://example.test"
        settings.VONAGE_API_KEY = "test-api-key"
        settings.VONAGE_SIGNATURE_SECRET = "test-signature-secret-key-32-bytes"

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        settings.PUBLIC_BASE_URL = self.previous_public_url
        settings.VONAGE_API_KEY = self.previous_vonage_api_key
        settings.VONAGE_SIGNATURE_SECRET = self.previous_signature_secret
        Base.metadata.drop_all(bind=self.engine)

    @staticmethod
    def vonage_headers():
        token = jwt.encode(
            {
                "iat": int(time.time()),
                "jti": uuid.uuid4().hex,
                "iss": "Vonage",
                "api_key": settings.VONAGE_API_KEY,
                "application_id": "test-application",
            },
            settings.VONAGE_SIGNATURE_SECRET,
            algorithm="HS256",
        )
        return {"Authorization": f"Bearer {token}"}

    def create_menu(self, **overrides):
        data = {
            "name": "Main menu",
            "welcome_message": "Press 1 for Sales.",
            "max_attempts": 2,
            "timeout_seconds": 5,
            "options": [{
                "digit": "1",
                "label": "Sales",
                "destination_phone": "+14155550123"
            }]
        }
        data.update(overrides)
        response = self.client.post("/api/ivr/menus/", json=data)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_create_and_list_menu(self):
        created = self.create_menu()
        listed = self.client.get("/api/ivr/menus/")

        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()[0]["id"], created["id"])
        self.assertEqual(listed.json()[0]["options"][0]["digit"], "1")

    def test_only_one_menu_remains_active(self):
        first = self.create_menu()
        second = self.create_menu(name="Alternate menu")
        menus = self.client.get("/api/ivr/menus/").json()

        self.assertTrue(next(menu for menu in menus if menu["id"] == second["id"])["is_active"])
        self.assertFalse(next(menu for menu in menus if menu["id"] == first["id"])["is_active"])

    def test_answer_returns_ncco_input_callback(self):
        self.create_menu()

        response = self.client.get("/api/voice/answer")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[1]["action"], "input")
        self.assertEqual(
            response.json()[1]["eventUrl"],
            ["https://example.test/api/voice/ivr/input?menu_id=1&attempt=1"]
        )

    def test_valid_digit_connects_to_configured_destination(self):
        self.create_menu()

        response = self.client.post(
            "/api/voice/ivr/input?menu_id=1&attempt=1",
            json={"dtmf": "1"},
            headers=self.vonage_headers()
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[1]["action"], "connect")
        self.assertEqual(
            response.json()[1]["endpoint"][0]["number"],
            "14155550123"
        )

    def test_invalid_digit_reprompts_then_ends_after_limit(self):
        self.create_menu()

        retry = self.client.post(
            "/api/voice/ivr/input?menu_id=1&attempt=1",
            json={"dtmf": "9"},
            headers=self.vonage_headers()
        )
        exhausted = self.client.post(
            "/api/voice/ivr/input?menu_id=1&attempt=2",
            json={"dtmf": "9"},
            headers=self.vonage_headers()
        )

        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json()[1]["action"], "input")
        self.assertIn("valid option", retry.json()[0]["text"].lower())
        self.assertEqual(exhausted.status_code, 200)
        self.assertEqual(len(exhausted.json()), 1)

    def test_timeout_reprompts_then_plays_timeout_message(self):
        self.create_menu(timeout_message="No response. Goodbye.")

        retry = self.client.post(
            "/api/voice/ivr/input?menu_id=1&attempt=1",
            json={},
            headers=self.vonage_headers()
        )
        exhausted = self.client.post(
            "/api/voice/ivr/input?menu_id=1&attempt=2",
            json={},
            headers=self.vonage_headers()
        )

        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json()[0]["text"], "No response. Goodbye.")
        self.assertEqual(exhausted.status_code, 200)
        self.assertEqual(exhausted.json()[0]["text"], "No response. Goodbye.")

    def test_duplicate_digits_are_rejected(self):
        response = self.client.post("/api/ivr/menus/", json={
            "name": "Duplicate menu",
            "welcome_message": "Choose.",
            "options": [
                {"digit": "1", "label": "Sales", "destination_phone": "+14155550123"},
                {"digit": "1", "label": "Support", "destination_phone": "+14155550124"}
            ]
        })

        self.assertEqual(response.status_code, 422)

    def test_ai_chat_requires_groq_configuration(self):
        previous_groq_key = settings.GROQ_API_KEY
        settings.GROQ_API_KEY = ""
        try:
            response = self.client.post(
                "/api/voice/ai/chat",
                json={"message": "Hello"}
            )
        finally:
            settings.GROQ_API_KEY = previous_groq_key

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["detail"],
            "Groq API key is not configured"
        )


if __name__ == "__main__":
    unittest.main()
