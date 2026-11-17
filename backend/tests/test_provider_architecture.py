import unittest
import asyncio
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers
from vonage_voice.models.requests import CreateCallRequest

from app.config.settings import settings
from app.services.ai_conversation_service import AIConversationService
from app.services.ai_provider import AIResponse
from app.services.edge_tts_provider import EdgeTTSProvider
from app.services.provider_registry import (
    ProviderConfigurationError,
    ProviderRegistry
)
from app.services.speech_processing_service import SpeechProcessingService
from app.services.speech_provider import SpeechRecognitionResult
from app.services.stt import GroqSpeechToTextProvider
from app.services.telephony_provider import CallRequest
from app.services.vonage_provider import VonageTelephonyProvider
from app.services.vonage_service import VonageService
from app.routes.voice_routes import (
    AIMessage,
    _read_audio_upload,
    ai_chat,
    speech_to_text,
)


class ProviderArchitectureTests(unittest.IsolatedAsyncioTestCase):

    def test_registry_selects_and_caches_configured_provider(self):
        config = SimpleNamespace(LLM_PROVIDER="groq")
        registry = ProviderRegistry(config)

        with patch(
            "app.services.provider_registry.GroqAIProvider"
        ) as provider_factory:
            first = registry.get_llm_provider()
            second = registry.get_llm_provider()

            self.assertIs(first, second)
            provider_factory.assert_called_once_with()

            registry.reset()
            registry.get_llm_provider()
            self.assertEqual(provider_factory.call_count, 2)

    def test_registry_rejects_unknown_provider_without_fallback(self):
        registry = ProviderRegistry(
            SimpleNamespace(LLM_PROVIDER="unknown")
        )

        with self.assertRaises(ProviderConfigurationError):
            registry.get_llm_provider()

    async def test_ai_conversation_service_uses_provider_interface(self):
        class FakeProvider:
            async def generate_response(self, messages, system_prompt):
                self.messages = messages
                self.system_prompt = system_prompt
                return AIResponse(content="  Hello there.  ", model="fake")

        provider = FakeProvider()
        service = AIConversationService(provider)

        reply = await service.generate_response("Hi")

        self.assertEqual(reply, "Hello there.")
        self.assertEqual(provider.messages[0].content, "Hi")
        self.assertIn("voice conversations", provider.system_prompt)

    def test_voice_chat_route_uses_registry_provider(self):
        class FakeProvider:
            async def generate_response(self, messages, system_prompt):
                return AIResponse(content="Hello from provider", model="fake")

        registry = SimpleNamespace(
            get_llm_provider=lambda: FakeProvider()
        )
        with patch.object(settings, "GROQ_API_KEY", "configured-for-test"):
            with patch(
                "app.routes.voice_routes.get_provider_registry",
                return_value=registry
            ):
                response = asyncio.run(
                    ai_chat(AIMessage(message="Hello"))
                )

        self.assertEqual(response["ai_response"], "Hello from provider")

    def test_stt_route_uses_registry_provider(self):
        class FakeSTTProvider:
            async def transcribe(self, audio_data, filename):
                return SpeechRecognitionResult(
                    text="recognized speech",
                    language="en"
                )

        registry = SimpleNamespace(
            get_stt_processing_service=lambda: SpeechProcessingService(
                stt_provider=FakeSTTProvider()
            )
        )
        upload = UploadFile(
            filename="sample.wav",
            file=BytesIO(b"audio"),
            headers=Headers({"content-type": "audio/wav"}),
        )
        with patch.object(settings, "GROQ_API_KEY", "configured-for-test"):
            with patch(
                "app.routes.voice_routes.get_provider_registry",
                return_value=registry
            ):
                response = asyncio.run(speech_to_text(upload))

        self.assertEqual(
            response["transcription"],
            "recognized speech"
        )

    def test_audio_upload_rejects_unsupported_media_type(self):
        upload = UploadFile(
            filename="sample.txt",
            file=BytesIO(b"not audio"),
            headers=Headers({"content-type": "text/plain"}),
        )

        with self.assertRaises(HTTPException) as raised:
            asyncio.run(_read_audio_upload(upload))

        self.assertEqual(raised.exception.status_code, 415)

    def test_audio_upload_rejects_content_over_configured_limit(self):
        upload = UploadFile(
            filename="sample.wav",
            file=BytesIO(b"audio bytes"),
            headers=Headers({"content-type": "audio/wav"}),
        )
        with patch.object(settings, "MAX_AUDIO_UPLOAD_BYTES", 5):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(_read_audio_upload(upload))

        self.assertEqual(raised.exception.status_code, 413)

    async def test_groq_stt_adapter_normalizes_text_and_filename(self):
        create = AsyncMock(return_value="  recognized words  ")
        client = SimpleNamespace(
            audio=SimpleNamespace(
                transcriptions=SimpleNamespace(create=create)
            )
        )
        provider = GroqSpeechToTextProvider(client=client)

        result = await provider.transcribe(b"audio bytes", "sample.wav")

        self.assertIsInstance(result, SpeechRecognitionResult)
        self.assertEqual(result.text, "recognized words")
        self.assertEqual(create.await_args.kwargs["file"], ("sample.wav", b"audio bytes"))
        self.assertEqual(
            create.await_args.kwargs["model"],
            settings.GROQ_STT_MODEL
        )

    async def test_edge_tts_adapter_collects_audio_stream(self):
        class FakeCommunicate:
            def __init__(self, text, voice, rate, volume):
                self.voice = voice

            async def stream(self):
                yield {"type": "WordBoundary", "data": b"ignored"}
                yield {"type": "audio", "data": b"mp3"}
                yield {"type": "audio", "data": b"bytes"}

        with patch(
            "app.services.edge_tts_provider.edge_tts.Communicate",
            FakeCommunicate
        ):
            audio = await EdgeTTSProvider().synthesize(
                "Hello",
                language="en",
                voice="en-US-AriaNeural"
            )

        self.assertEqual(audio, b"mp3bytes")

    def test_vonage_adapter_maps_call_and_control_operations(self):
        class FakeVoice:
            def create_call(self, payload):
                self.call_payload = payload
                self.validated_request = CreateCallRequest.model_validate(payload)
                return SimpleNamespace(uuid="call-123", status="started")

            def transfer_call_ncco(self, call_uuid, ncco):
                self.transfer = (call_uuid, ncco)

            def hangup(self, call_uuid):
                self.hung_up = call_uuid

        voice = FakeVoice()
        service = VonageService(client=SimpleNamespace(voice=voice))
        provider = VonageTelephonyProvider(service=service)

        with patch.object(settings, "VONAGE_NUMBER", "+14155550124"):
            result = provider.create_call(
                CallRequest(
                    phone_number="+14155550123",
                    answer_url="https://example.test/answer",
                    event_url="https://example.test/events"
                )
            )
            transferred = provider.transfer_call(
                "call-123",
                "+14155550125"
            )
            hung_up = provider.hangup_call("call-123")

        self.assertEqual(result.call_uuid, "call-123")
        self.assertEqual(result.status, "started")
        self.assertEqual(voice.validated_request.to[0].number, "+14155550123")
        self.assertEqual(voice.validated_request.from_.number, "+14155550124")
        self.assertEqual(
            voice.validated_request.answer_url,
            ["https://example.test/answer"]
        )
        self.assertTrue(transferred)
        self.assertEqual(voice.transfer[0], "call-123")
        self.assertTrue(hung_up)
        self.assertEqual(voice.hung_up, "call-123")


if __name__ == "__main__":
    unittest.main()
