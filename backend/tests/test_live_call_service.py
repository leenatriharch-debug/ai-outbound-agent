import asyncio
import io
import unittest
import wave
from array import array
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.connection import Base
from app.models.call_attempt import CallAttempt
from app.models.call_dispatch_job import CallDispatchJob
from app.models.campaign import Campaign
from app.models.campaign_contact import CampaignContact
from app.models.conversation_attempt_link import ConversationAttemptLink
from app.models.conversation_message import ConversationMessage
from app.models.conversation_session import ConversationSession
from app.models.customer import Customer
from app.services.ai_conversation_service import AIConversationService
from app.services.ai_provider import AIResponse
from app.services.live_call_service import (
    AudioTranscodeError,
    AudioTranscoder,
    LiveCallConversationService,
    PcmTurnSegmenter,
    VonageMediaStreamHandler,
)


class FakeSpeechService:
    def __init__(self, transcriptions):
        self.transcriptions = iter(transcriptions)
        self.transcription_audio = []
        self.synthesized_text = []

    async def convert_audio_to_text(self, audio_data, filename):
        self.transcription_audio.append((audio_data, filename))
        return SimpleNamespace(text=next(self.transcriptions))

    async def convert_text_to_audio(self, text):
        self.synthesized_text.append(text)
        return b"mock encoded audio"


class FakeAudioTranscoder:
    @staticmethod
    def to_pcm16_mono_16k(encoded_audio):
        return b"\xe8\x03" * 320


class FakeAIProvider:
    def __init__(self):
        self.requests = []

    async def generate_response(self, messages, system_prompt):
        self.requests.append((list(messages), system_prompt))
        return AIResponse(
            content=f"answer {len(self.requests)}",
            model="mock",
        )


class FakeWebSocket:
    def __init__(self, messages):
        self.messages = iter(messages)
        self.sent_audio = []
        self.sent_text = []
        self.closed = []
        self.client_state = SimpleNamespace(name="CONNECTED")

    async def accept(self):
        return None

    async def receive(self):
        await asyncio.sleep(0.005)
        return next(self.messages)

    async def send_bytes(self, data):
        self.sent_audio.append(data)

    async def send_text(self, data):
        self.sent_text.append(data)

    async def close(self, code=1000, reason=None):
        self.closed.append((code, reason))
        self.client_state.name = "DISCONNECTED"


class ImmediateFakeWebSocket(FakeWebSocket):
    async def receive(self):
        return next(self.messages)


class FakeLiveConversation:
    dispatch_job_id = 1

    def __init__(self):
        self.utterances = []
        self.closed_status = None
        self.greeting_calls = 0

    async def start(self):
        return None

    async def greet(self):
        self.greeting_calls += 1
        return "hello", b"\xe8\x03" * 960

    async def process_utterance(self, audio):
        await asyncio.sleep(0)
        self.utterances.append(audio)
        return "reply", b"\xe8\x03" * 320

    async def transcribe_utterance(self, audio):
        self.utterances.append(audio)
        return "partial transcript"

    async def synthesize_assistant(self, text):
        return text, b"\xe8\x03" * 320

    async def close(self, status):
        self.closed_status = status


class LiveCallServiceTests(unittest.IsolatedAsyncioTestCase):
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

    def _create_dispatch_job(self, suffix):
        campaign = Campaign(
            name=f"Live call {suffix}",
            status="active",
            total_contacts=1,
        )
        customer = Customer(
            full_name=f"Customer {suffix}",
            phone_number=f"+14155550{suffix:04d}",
            is_active=True,
        )
        self.db.add_all([campaign, customer])
        self.db.flush()
        contact = CampaignContact(
            campaign_id=campaign.id,
            customer_id=customer.id,
            status="calling",
            attempts=1,
        )
        self.db.add(contact)
        self.db.flush()
        attempt = CallAttempt(
            campaign_contact_id=contact.id,
            attempt_number=1,
            status="initiated",
            call_uuid=f"mock-call-{suffix}",
        )
        self.db.add(attempt)
        self.db.flush()
        job = CallDispatchJob(
            call_attempt_id=attempt.id,
            status="dispatched",
            provider_call_uuid=attempt.call_uuid,
        )
        self.db.add(job)
        self.db.commit()
        return attempt, job

    def _conversation(self, job, texts, provider):
        return LiveCallConversationService(
            dispatch_job_id=job.id,
            session_factory=self.session_factory,
            ai_service=AIConversationService(provider),
            speech_service=FakeSpeechService(texts),
            transcoder=FakeAudioTranscoder,
        )

    async def test_transcripts_history_and_attempt_link_are_persisted_per_call(self):
        attempt_one, job_one = self._create_dispatch_job(1)
        attempt_two, job_two = self._create_dispatch_job(2)
        provider_one = FakeAIProvider()
        provider_two = FakeAIProvider()
        one = self._conversation(job_one, ["first question", "second question"], provider_one)
        two = self._conversation(job_two, ["other customer"], provider_two)

        await one.start()
        await one.greet()
        await one.process_utterance(bytes(640))
        await one.process_utterance(bytes(640))
        await two.start()
        await two.process_utterance(bytes(640))
        await one.close()
        await two.close()

        self.db.expire_all()
        link_one = self.db.query(ConversationAttemptLink).filter_by(
            call_attempt_id=attempt_one.id
        ).one()
        link_two = self.db.query(ConversationAttemptLink).filter_by(
            call_attempt_id=attempt_two.id
        ).one()
        self.assertNotEqual(
            link_one.conversation_session_id,
            link_two.conversation_session_id,
        )

        messages_one = self.db.query(ConversationMessage).filter_by(
            session_id=link_one.conversation_session_id
        ).order_by(ConversationMessage.id).all()
        self.assertEqual(
            [message.message for message in messages_one],
            [
                "Hello! This is Worldotech's AI assistant. "
                "How can I help you today?",
                "first question",
                "answer 1",
                "second question",
                "answer 2",
            ],
        )
        second_request = provider_one.requests[1][0]
        self.assertEqual(
            [(message.role, message.content) for message in second_request],
            [
                (
                    "assistant",
                    "Hello! This is Worldotech's AI assistant. "
                    "How can I help you today?",
                ),
                ("user", "first question"),
                ("assistant", "answer 1"),
                ("user", "second question"),
            ],
        )
        self.assertEqual(
            [(message.role, message.content) for message in provider_two.requests[0][0]],
            [("user", "other customer")],
        )

    async def test_vad_segments_speech_and_transcoder_outputs_16khz_pcm(self):
        segmenter = PcmTurnSegmenter(
            threshold=500,
            silence_ms=40,
            max_utterance_seconds=1,
        )
        speech = array("h", [1000] * 320).tobytes()
        silence = bytes(640)

        started, utterance = segmenter.feed(speech)
        self.assertTrue(started)
        self.assertIsNone(utterance)
        self.assertIsNone(segmenter.feed(silence)[1])
        utterance = segmenter.feed(silence)[1]
        self.assertIsNotNone(utterance)
        self.assertIn(speech, utterance)

        source = io.BytesIO()
        with wave.open(source, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16000)
            wav_file.writeframes(speech)
        converted = AudioTranscoder.to_pcm16_mono_16k(source.getvalue())
        self.assertGreater(len(converted), 0)
        self.assertEqual(len(converted) % 2, 0)

    async def test_interruption_sends_clear_and_disconnect_closes_session(self):
        speech = array("h", [1000] * 320).tobytes()
        websocket = FakeWebSocket([
            {"type": "websocket.receive", "bytes": speech},
            {"type": "websocket.receive", "bytes": bytes(640)},
            {"type": "websocket.receive", "bytes": bytes(640)},
            {"type": "websocket.disconnect", "code": 1000},
        ])
        conversation = FakeLiveConversation()
        handler = VonageMediaStreamHandler(
            conversation=conversation,
            telephony_provider=SimpleNamespace(),
            call_uuid=None,
            segmenter=PcmTurnSegmenter(
                threshold=500,
                silence_ms=20,
                max_utterance_seconds=1,
            ),
            silence_timeout=60,
        )

        await handler.run(websocket)

        self.assertIn('{"action":"clear"}', websocket.sent_text)
        self.assertEqual(conversation.closed_status, "completed")
        self.assertEqual(len(conversation.utterances), 1)

    async def test_initial_greeting_is_not_cancelled_by_first_inbound_speech(self):
        speech = array("h", [1000] * 320).tobytes()
        websocket = ImmediateFakeWebSocket([
            {"type": "websocket.receive", "bytes": speech},
            {"type": "websocket.disconnect", "code": 1000},
        ])
        conversation = FakeLiveConversation()
        handler = VonageMediaStreamHandler(
            conversation=conversation,
            telephony_provider=SimpleNamespace(),
            call_uuid=None,
            silence_timeout=60,
        )

        await handler.run(websocket)

        self.assertEqual(conversation.greeting_calls, 1)
        self.assertEqual(len(websocket.sent_audio), 3)
        self.assertTrue(all(any(frame) for frame in websocket.sent_audio))
        self.assertEqual(conversation.closed_status, "completed")

    async def test_silent_pcm_is_not_sent_to_vonage(self):
        websocket = FakeWebSocket([])
        handler = VonageMediaStreamHandler(
            conversation=FakeLiveConversation(),
            telephony_provider=SimpleNamespace(),
            call_uuid=None,
        )
        handler.websocket = websocket

        with self.assertRaises(AudioTranscodeError):
            await handler._play_audio(bytes(640))

        self.assertEqual(websocket.sent_audio, [])

    async def test_media_stream_handles_control_events_and_multiple_turns(self):
        speech = array("h", [1000] * 320).tobytes()
        websocket = FakeWebSocket([
            {
                "type": "websocket.receive",
                "text": '{"event":"websocket:connected"}',
            },
            {"type": "websocket.receive", "bytes": speech},
            {"type": "websocket.receive", "bytes": bytes(640)},
            {"type": "websocket.receive", "bytes": speech},
            {"type": "websocket.receive", "bytes": bytes(640)},
            {"type": "websocket.disconnect", "code": 1000},
        ])
        conversation = FakeLiveConversation()
        handler = VonageMediaStreamHandler(
            conversation=conversation,
            telephony_provider=SimpleNamespace(),
            call_uuid=None,
            segmenter=PcmTurnSegmenter(
                threshold=500,
                silence_ms=20,
                max_utterance_seconds=1,
            ),
            silence_timeout=60,
        )

        with self.assertLogs(
            "app.services.live_call_service",
            level="INFO",
        ) as captured:
            await handler.run(websocket)

        self.assertIn(
            "Vonage media control event received",
            "\n".join(captured.output),
        )
        self.assertEqual(len(conversation.utterances), 2)
        self.assertEqual(len(websocket.sent_audio), 5)
        self.assertEqual(conversation.closed_status, "completed")

    async def test_websocket_runs_stt_llm_tts_for_multiple_turns(self):
        attempt, job = self._create_dispatch_job(3)
        provider = FakeAIProvider()
        conversation = self._conversation(
            job,
            ["first question", "second question"],
            provider,
        )
        speech = array("h", [1000] * 320).tobytes()
        websocket = FakeWebSocket([
            {
                "type": "websocket.receive",
                "text": '{"event":"websocket:connected"}',
            },
            {"type": "websocket.receive", "bytes": speech},
            {"type": "websocket.receive", "bytes": bytes(640)},
            {"type": "websocket.receive", "bytes": speech},
            {"type": "websocket.receive", "bytes": bytes(640)},
            {"type": "websocket.disconnect", "code": 1000},
        ])
        handler = VonageMediaStreamHandler(
            conversation=conversation,
            telephony_provider=SimpleNamespace(),
            call_uuid=attempt.call_uuid,
            segmenter=PcmTurnSegmenter(
                threshold=500,
                silence_ms=20,
                max_utterance_seconds=1,
            ),
            silence_timeout=60,
        )

        await handler.run(websocket)

        self.db.expire_all()
        link = self.db.query(ConversationAttemptLink).filter_by(
            call_attempt_id=attempt.id
        ).one()
        messages = self.db.query(ConversationMessage).filter_by(
            session_id=link.conversation_session_id
        ).order_by(ConversationMessage.id).all()

        self.assertEqual(
            [message.role for message in messages],
            ["assistant", "user", "assistant", "user", "assistant"],
        )
        self.assertEqual(
            [message.message for message in messages[1::2]],
            ["first question", "second question"],
        )
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(len(websocket.sent_audio), 3)
        self.assertTrue(all(len(frame) == 640 for frame in websocket.sent_audio))
        self.assertTrue(all(any(frame) for frame in websocket.sent_audio))
        self.assertEqual(conversation.conversation_id, link.conversation_session_id)

    async def test_response_failure_hangs_up_call_and_marks_session_failed(self):
        class FailingConversation(FakeLiveConversation):
            async def process_utterance(self, audio):
                raise RuntimeError("mock response failure")

        class FakeTelephonyProvider:
            def __init__(self):
                self.hung_up = []

            def hangup_call(self, call_uuid):
                self.hung_up.append(call_uuid)
                return True

        speech = array("h", [1000] * 320).tobytes()
        websocket = FakeWebSocket([
            {"type": "websocket.receive", "bytes": speech},
            {"type": "websocket.receive", "bytes": bytes(640)},
            {"type": "websocket.disconnect", "code": 1000},
        ])
        conversation = FailingConversation()
        telephony = FakeTelephonyProvider()
        handler = VonageMediaStreamHandler(
            conversation=conversation,
            telephony_provider=telephony,
            call_uuid="mock-call",
            segmenter=PcmTurnSegmenter(
                threshold=500,
                silence_ms=20,
                max_utterance_seconds=1,
            ),
            silence_timeout=60,
        )

        await handler.run(websocket)

        self.assertEqual(telephony.hung_up, ["mock-call"])
        self.assertEqual(conversation.closed_status, "failed")
        self.assertTrue(websocket.closed)

    async def test_silence_reprompts_then_hangs_up(self):
        class FakeTelephonyProvider:
            def __init__(self):
                self.hung_up = []

            def hangup_call(self, call_uuid):
                self.hung_up.append(call_uuid)
                return True

        websocket = FakeWebSocket([])
        conversation = FakeLiveConversation()
        telephony = FakeTelephonyProvider()
        handler = VonageMediaStreamHandler(
            conversation=conversation,
            telephony_provider=telephony,
            call_uuid="silent-call",
            silence_timeout=0,
            max_reprompts=1,
        )
        handler.websocket = websocket
        handler.running = True

        await asyncio.wait_for(handler._watch_silence(), timeout=2)

        self.assertEqual(telephony.hung_up, ["silent-call"])
        self.assertEqual(websocket.closed[0][0], 1000)
        self.assertEqual(conversation.closed_status, None)


if __name__ == "__main__":
    unittest.main()
