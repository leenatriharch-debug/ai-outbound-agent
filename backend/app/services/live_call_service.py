import array
import asyncio
import io
import json
import logging
import math
import re
import sys
import time
import wave
from collections import deque
from datetime import datetime, timezone

import av
from sqlalchemy.orm import Session

from app.core.config import settings
from app.database.connection import SessionLocal
from app.models.call import Call
from app.models.call_attempt import CallAttempt
from app.models.call_dispatch_job import CallDispatchJob
from app.models.campaign_contact import CampaignContact
from app.models.conversation_attempt_link import ConversationAttemptLink
from app.models.conversation_session import ConversationSession
from app.services.ai_provider import AIMessage
from app.services.conversation_manager import ConversationManager
from app.services.provider_registry import get_provider_registry
from app.services.rag_chat_service import RAGAPIError, RAGChatService
from app.services.speech_processing_service import SpeechProcessingService

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2
PLAYBACK_FRAME_BYTES = 640
PLAYBACK_FRAME_SECONDS = PLAYBACK_FRAME_BYTES / (SAMPLE_RATE * SAMPLE_WIDTH)
VOICE_RESPONSE_TIMEOUT_SECONDS = 45


class AudioTranscodeError(RuntimeError):
    pass


def _normalize_command(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def _is_repeat_request(text: str) -> bool:
    return _normalize_command(text) in {
        "repeat",
        "please repeat",
        "say that again",
        "repeat that",
        "dobara",
        "dobara boliye",
        "phir se",
        "phir se boliye",
    }


def _is_exit_request(text: str) -> bool:
    return _normalize_command(text) in {
        "bye",
        "goodbye",
        "end call",
        "hang up",
        "exit",
        "stop",
        "alvida",
        "call band",
        "call band karo",
        "call kaat do",
        "band karo",
        "mujhe call band karna hai",
    }


_HINDI_LATIN_MARKERS = {
    "aap",
    "batao",
    "chahiye",
    "hai",
    "hain",
    "ka",
    "kahaan",
    "kahan",
    "kaise",
    "kar",
    "karna",
    "ke",
    "ki",
    "kitna",
    "kitni",
    "ko",
    "kya",
    "mein",
    "mera",
    "meri",
    "mujhe",
    "nahi",
    "nahin",
    "se",
    "yeh",
}


def _voice_language(language: str | None, text: str = "") -> str:
    if language and language.lower().split("-")[0] in {"hi", "hindi"}:
        return "hi"
    if any("\u0900" <= character <= "\u097f" for character in text):
        return "hi"
    words = set(_normalize_command(text).split())
    if len(words & _HINDI_LATIN_MARKERS) >= 2:
        return "hi"
    return "en"


def pcm16_to_wav(pcm: bytes) -> bytes:
    if not pcm or len(pcm) % SAMPLE_WIDTH:
        raise ValueError("PCM audio must contain complete 16-bit samples")

    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(SAMPLE_WIDTH)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(pcm)
    return output.getvalue()


class AudioTranscoder:
    @staticmethod
    def to_pcm16_mono_16k(encoded_audio: bytes) -> bytes:
        if not encoded_audio:
            raise AudioTranscodeError("The TTS provider returned empty audio")

        try:
            pcm = bytearray()
            with av.open(io.BytesIO(encoded_audio), mode="r") as container:
                audio_stream = next(
                    (stream for stream in container.streams if stream.type == "audio"),
                    None,
                )
                if audio_stream is None:
                    raise AudioTranscodeError("The TTS output has no audio stream")

                resampler = av.AudioResampler(
                    format="s16",
                    layout="mono",
                    rate=SAMPLE_RATE,
                )
                for frame in container.decode(audio=audio_stream.index):
                    for converted in resampler.resample(frame):
                        pcm.extend(
                            bytes(converted.planes[0])[
                                :converted.samples * SAMPLE_WIDTH
                            ]
                        )

                for converted in resampler.resample(None):
                    pcm.extend(
                        bytes(converted.planes[0])[
                            :converted.samples * SAMPLE_WIDTH
                        ]
                    )

            if not pcm:
                raise AudioTranscodeError("The TTS output could not be decoded")
            if sys.byteorder == "big":
                pcm[0::2], pcm[1::2] = pcm[1::2], pcm[0::2]
            return bytes(pcm)
        except AudioTranscodeError:
            raise
        except (av.error.FFmpegError, OSError, ValueError) as error:
            raise AudioTranscodeError(
                "The TTS audio could not be converted to 16-kHz PCM"
            ) from error


class PcmTurnSegmenter:
    def __init__(
        self,
        threshold: int = settings.CALL_VAD_THRESHOLD,
        silence_ms: int = settings.CALL_VAD_SILENCE_MS,
        max_utterance_seconds: int = settings.CALL_MAX_UTTERANCE_SECONDS,
        pre_roll_ms: int = 200,
    ):
        if threshold <= 0 or silence_ms <= 0 or max_utterance_seconds <= 0:
            raise ValueError("VAD thresholds and utterance limits must be positive")
        self.threshold = threshold
        self.silence_bytes = SAMPLE_RATE * SAMPLE_WIDTH * silence_ms // 1000
        self.max_utterance_bytes = (
            SAMPLE_RATE * SAMPLE_WIDTH * max_utterance_seconds
        )
        self._pre_roll = deque(
            maxlen=max(1, pre_roll_ms // 20),
        )
        self._audio = bytearray()
        self._silence_bytes_seen = 0
        self._speaking = False

    @staticmethod
    def _rms(frame: bytes) -> float:
        if len(frame) % SAMPLE_WIDTH:
            raise ValueError("Audio frame must contain complete 16-bit samples")

        samples = array.array("h")
        samples.frombytes(frame)
        if sys.byteorder == "big":
            samples.byteswap()
        if not samples:
            return 0
        return math.sqrt(sum(sample * sample for sample in samples) / len(samples))

    def feed(self, frame: bytes) -> tuple[bool, bytes | None]:
        if not frame:
            return False, None

        speech_started = self._rms(frame) >= self.threshold
        if speech_started:
            just_started = not self._speaking
            if just_started:
                self._audio = bytearray(b"".join(self._pre_roll))
                self._pre_roll.clear()
                self._speaking = True
            self._audio.extend(frame)
            self._silence_bytes_seen = 0
            if len(self._audio) >= self.max_utterance_bytes:
                utterance = bytes(self._audio)
                self._reset()
                return just_started, utterance
            return just_started, None

        if not self._speaking:
            self._pre_roll.append(frame)
            return False, None

        self._audio.extend(frame)
        self._silence_bytes_seen += len(frame)
        if self._silence_bytes_seen >= self.silence_bytes:
            utterance = bytes(self._audio)
            self._reset()
            return False, utterance
        return False, None

    def finish(self) -> bytes | None:
        utterance = bytes(self._audio) if self._speaking and self._audio else None
        self._reset()
        return utterance

    def _reset(self):
        self._audio.clear()
        self._pre_roll.clear()
        self._silence_bytes_seen = 0
        self._speaking = False


class LiveCallConversationService:
    def __init__(
        self,
        dispatch_job_id: int | None,
        session_factory=SessionLocal,
        rag_service: RAGChatService | None = None,
        speech_service: SpeechProcessingService | None = None,
        transcoder=AudioTranscoder,
        inbound_call_id: int | None = None,
        inbound_call_uuid: str | None = None,
    ):
        registry = get_provider_registry()
        self.dispatch_job_id = dispatch_job_id
        self.inbound_call_id = inbound_call_id
        self.inbound_call_uuid = inbound_call_uuid
        self.session_factory = session_factory
        self.rag_service = rag_service or RAGChatService()
        self.speech_service = speech_service or registry.get_voice_processing_service()
        self.transcoder = transcoder
        self.conversation_id: int | None = None
        self.history: list[AIMessage] = []
        self.caller_language = "en"
        self.should_end_call = False

    async def start(self):
        if self.dispatch_job_id is not None:
            self.conversation_id = await asyncio.to_thread(self._create_session)
        elif self.inbound_call_id is not None:
            self.conversation_id = await asyncio.to_thread(
                self._create_inbound_session
            )
        else:
            raise ValueError("A call dispatch or inbound call is required")
        messages = await asyncio.to_thread(self._load_history)
        self.history = [
            AIMessage(role=message.role, content=message.message)
            for message in messages
            if message.role in {"user", "assistant"}
        ]

    def _create_session(self) -> int:
        db: Session = self.session_factory()
        try:
            job = db.query(CallDispatchJob).filter(
                CallDispatchJob.id == self.dispatch_job_id
            ).with_for_update().first()
            if not job:
                raise ValueError("Call dispatch job was not found")

            customer_id = None
            call_id = job.call_id
            attempt_id = job.call_attempt_id
            if attempt_id:
                attempt = db.query(CallAttempt).filter(
                    CallAttempt.id == attempt_id
                ).first()
                if not attempt:
                    raise ValueError("Call attempt was not found")
                contact = db.query(CampaignContact).filter(
                    CampaignContact.id == attempt.campaign_contact_id
                ).first()
                customer_id = contact.customer_id if contact else None
                existing_link = db.query(ConversationAttemptLink).filter(
                    ConversationAttemptLink.call_attempt_id == attempt_id
                ).first()
                if existing_link:
                    existing = db.query(ConversationSession).filter(
                        ConversationSession.id
                        == existing_link.conversation_session_id
                    ).first()
                    if not existing or existing.status != "active":
                        raise ValueError(
                            "A conversation already exists for this call attempt"
                        )
                    return existing.id
            elif call_id:
                call = db.query(Call).filter(Call.id == call_id).first()
                if not call:
                    raise ValueError("Outbound call was not found")
            else:
                raise ValueError("Call dispatch job has no call target")

            session_key = f"call-dispatch-{self.dispatch_job_id}"
            session = db.query(ConversationSession).filter(
                ConversationSession.session_id == session_key
            ).first()
            if session:
                if session.status != "active":
                    raise ValueError("The call conversation has already ended")
                if attempt_id:
                    db.add(ConversationAttemptLink(
                        conversation_session_id=session.id,
                        call_attempt_id=attempt_id,
                    ))
                db.commit()
                return session.id

            session = ConversationSession(
                session_id=session_key,
                customer_id=customer_id,
                call_id=call_id,
                status="active",
            )
            db.add(session)
            db.flush()
            if attempt_id:
                db.add(ConversationAttemptLink(
                    conversation_session_id=session.id,
                    call_attempt_id=attempt_id,
                ))
            db.commit()
            return session.id
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _create_inbound_session(self) -> int:
        if not self.inbound_call_uuid:
            raise ValueError("Inbound call UUID was not provided")
        db: Session = self.session_factory()
        try:
            call = db.query(Call).filter(
                Call.id == self.inbound_call_id,
                Call.call_uuid == self.inbound_call_uuid,
            ).first()
            if not call:
                raise ValueError("Inbound call was not found")

            session_key = f"inbound-call-{call.id}"
            session = db.query(ConversationSession).filter(
                ConversationSession.session_id == session_key
            ).first()
            if session:
                if session.status != "active":
                    raise ValueError(
                        "The inbound call conversation has ended"
                    )
                return session.id

            session = ConversationSession(
                session_id=session_key,
                call_id=call.id,
                status="active",
            )
            db.add(session)
            db.commit()
            return session.id
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _load_history(self):
        if self.conversation_id is None:
            raise RuntimeError("Conversation has not been started")
        db: Session = self.session_factory()
        try:
            return ConversationManager.get_conversation_history(
                db,
                self.conversation_id,
            )
        finally:
            db.close()

    def _persist_message(self, role: str, message: str):
        if self.conversation_id is None:
            raise RuntimeError("Conversation has not been started")
        db: Session = self.session_factory()
        try:
            return ConversationManager.add_message(
                db,
                self.conversation_id,
                role,
                message,
            )
        finally:
            db.close()

    async def greet(self) -> tuple[str, bytes]:
        greeting = settings.CALL_GREETING.strip()
        if not greeting:
            raise ValueError("CALL_GREETING must not be empty")
        return await self.synthesize_assistant(greeting)

    async def process_utterance(
        self,
        pcm: bytes,
    ) -> tuple[str, bytes] | None:
        prior_history = list(self.history)
        user_text = await self.transcribe_utterance(pcm)
        if not user_text:
            return None

        if _is_repeat_request(user_text):
            assistant_text = next(
                (
                    message.content
                    for message in reversed(prior_history)
                    if message.role == "assistant"
                ),
                "I haven't answered a question yet. Please ask me one.",
            )
            return await self.synthesize_assistant(assistant_text)

        if _is_exit_request(user_text):
            self.should_end_call = True
            assistant_text = (
                "Theek hai, call yahin khatam karte hain. Namaste."
                if self.caller_language == "hi"
                else "Okay, ending the call now. Goodbye."
            )
            return await self.synthesize_assistant(assistant_text)

        started = time.perf_counter()
        try:
            assistant_text = await self.rag_service.get_answer(
                user_text,
                history=prior_history,
            )
        except RAGAPIError as error:
            logger.warning(
                "WorldoTutors RAG API request failed",
                extra={
                    "event": "voice_rag_failed",
                    "dispatch_job_id": self.dispatch_job_id,
                    "inbound_call_id": self.inbound_call_id,
                    "failure_type": error.reason,
                    "status_code": error.status_code,
                },
            )
            assistant_text = (
                "Maaf kijiye, abhi mujhe iska jawab nahi mil raha. "
                "Aap dobara pooch sakte hain."
                if self.caller_language == "hi"
                else "Sorry, I can't get that answer right now. "
                "Please try asking again later."
            )
        if not assistant_text:
            raise RuntimeError("The RAG API returned an empty response")
        logger.info(
            "Live call RAG response generated",
            extra={
                "event": "voice_rag_response",
                "dispatch_job_id": self.dispatch_job_id,
                "inbound_call_id": self.inbound_call_id,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "text_length": len(assistant_text),
            },
        )
        return await self.synthesize_assistant(assistant_text)

    async def transcribe_utterance(self, pcm: bytes) -> str:
        transcription = await self.speech_service.convert_audio_to_text(
            pcm16_to_wav(pcm),
            "live-utterance.wav",
        )
        user_text = transcription.text.strip()
        self.caller_language = _voice_language(
            getattr(transcription, "language", None),
            user_text,
        )
        logger.info(
            "Live call speech transcription completed",
            extra={
                "event": "voice_transcription",
                "dispatch_job_id": self.dispatch_job_id,
                "audio_bytes": len(pcm),
                "text_length": len(user_text),
            },
        )
        if user_text:
            await asyncio.to_thread(self._persist_message, "user", user_text)
            self.history.append(AIMessage(role="user", content=user_text))
        return user_text

    async def synthesize_assistant(
        self,
        text: str,
    ) -> tuple[str, bytes]:
        await asyncio.to_thread(self._persist_message, "assistant", text)
        self.history.append(AIMessage(role="assistant", content=text))
        started = time.perf_counter()
        encoded_audio = await self.speech_service.convert_text_to_audio(
            text,
            language=self.caller_language,
        )
        pcm = await asyncio.to_thread(
            self.transcoder.to_pcm16_mono_16k,
            encoded_audio,
        )
        logger.info(
            "Live call TTS audio generated",
            extra={
                "event": "voice_tts_audio",
                "dispatch_job_id": self.dispatch_job_id,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "audio_bytes": len(pcm),
                "audio_duration_ms": round(
                    len(pcm) * 1000 / (SAMPLE_RATE * SAMPLE_WIDTH),
                    2,
                ),
            },
        )
        return text, pcm

    async def close(self, status: str = "completed"):
        if self.conversation_id is None:
            return
        await asyncio.to_thread(self._close_session, status)

    def _close_session(self, status: str):
        db: Session = self.session_factory()
        try:
            now = datetime.now(timezone.utc)
            session = db.query(ConversationSession).filter(
                ConversationSession.id == self.conversation_id
            ).first()
            if session and session.status == "active":
                session.status = status
                session.ended_at = now
            if self.inbound_call_id is not None:
                call = db.query(Call).filter(
                    Call.id == self.inbound_call_id
                ).first()
                if call:
                    call.status = status
                    call.completed_at = now
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

class VonageMediaStreamHandler:
    def __init__(
        self,
        conversation: LiveCallConversationService,
        telephony_provider,
        call_uuid: str | None,
        segmenter: PcmTurnSegmenter | None = None,
        silence_timeout: int = settings.CALL_SILENCE_TIMEOUT_SECONDS,
        max_reprompts: int = settings.CALL_SILENCE_REPROMPTS,
    ):
        self.conversation = conversation
        self.telephony_provider = telephony_provider
        self.call_uuid = call_uuid
        self.segmenter = segmenter or PcmTurnSegmenter()
        self.silence_timeout = silence_timeout
        self.max_reprompts = max_reprompts
        self.last_activity = time.monotonic()
        self.reprompt_count = 0
        self.running = False
        self.response_task: asyncio.Task | None = None
        self.send_lock = asyncio.Lock()
        self.websocket = None
        self.response_failure = asyncio.Event()
        self.response_error: Exception | None = None
        self.received_audio_frames = 0
        self.received_audio_bytes = 0
        self.unrecognized_count = 0

    async def run(self, websocket):
        self.websocket = websocket
        await websocket.accept()
        logger.info(
            "Vonage media WebSocket accepted",
            extra={
                "event": "media_websocket_started",
                "dispatch_job_id": self.conversation.dispatch_job_id,
                "audio_format": "audio/l16;rate=16000",
            },
        )
        status = "completed"
        self.running = True
        watchdog = None
        receive_task = None
        failure_task = None
        try:
            await self.conversation.start()
            await self._greet()
            watchdog = asyncio.create_task(self._watch_silence())
            receive_task = asyncio.create_task(self._receive_audio())
            failure_task = asyncio.create_task(self.response_failure.wait())
            done, _ = await asyncio.wait(
                (receive_task, failure_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if failure_task in done and self.response_error:
                raise RuntimeError(
                    "A live call response task failed"
                ) from self.response_error
            await receive_task
        except asyncio.CancelledError:
            status = "failed"
            raise
        except Exception:
            status = "failed"
            logger.exception(
                "Live call conversation failed for dispatch job %s",
                self.conversation.dispatch_job_id,
                extra={
                    "event": "live_call_failed",
                    "dispatch_job_id": self.conversation.dispatch_job_id,
                },
            )
            await self._hangup_call("voice_processing_failed")
            if getattr(websocket.client_state, "name", "") == "CONNECTED":
                await websocket.close(
                    code=1011,
                    reason="Voice processing failed",
                )
        finally:
            self.running = False
            background_tasks = [
                task for task in (watchdog, receive_task, failure_task)
                if task is not None
            ]
            for task in background_tasks:
                if not task.done():
                    task.cancel()
            if background_tasks:
                await asyncio.gather(
                    *background_tasks,
                    return_exceptions=True,
                )
            await self._cancel_response()
            partial_utterance = self.segmenter.finish()
            if partial_utterance:
                try:
                    await self.conversation.transcribe_utterance(
                        partial_utterance
                    )
                except Exception:
                    logger.exception(
                        "Could not transcribe the final audio for dispatch job %s",
                        self.conversation.dispatch_job_id,
                    )
            try:
                await self.conversation.close(status)
            except Exception:
                logger.exception(
                    "Could not close the conversation for dispatch job %s",
                    self.conversation.dispatch_job_id,
                )
                raise
            logger.info(
                "Live call media session ended",
                extra={
                    "event": "media_session_ended",
                    "dispatch_job_id": self.conversation.dispatch_job_id,
                    "status": status,
                    "audio_frames": self.received_audio_frames,
                    "audio_bytes": self.received_audio_bytes,
                },
            )

    async def _receive_audio(self):
        try:
            while self.running:
                message = await self.websocket.receive()
                if message["type"] == "websocket.disconnect":
                    logger.info(
                        "Vonage media WebSocket disconnected",
                        extra={
                            "event": "media_websocket_disconnected",
                            "dispatch_job_id":
                                self.conversation.dispatch_job_id,
                            "connection_code": message.get("code"),
                        },
                    )
                    return

                frame = message.get("bytes")
                if frame is None:
                    text = message.get("text")
                    if text:
                        self._handle_control_message(text)
                    continue
                if len(frame) > settings.CALL_MAX_AUDIO_FRAME_BYTES:
                    raise ValueError(
                        "Inbound audio frame exceeds the configured limit"
                    )

                self.received_audio_frames += 1
                self.received_audio_bytes += len(frame)
                speech_started, utterance = self.segmenter.feed(frame)
                if speech_started:
                    self.last_activity = time.monotonic()
                    self.reprompt_count = 0
                    await self._interrupt()
                if utterance:
                    self.response_task = self._create_response_task(
                        self._respond_to_utterance(utterance),
                        stage="utterance_response",
                    )
        finally:
            logger.info(
                "Vonage media audio received",
                extra={
                    "event": "media_audio_received",
                    "dispatch_job_id": self.conversation.dispatch_job_id,
                    "audio_frames": self.received_audio_frames,
                    "audio_bytes": self.received_audio_bytes,
                },
            )

    def _handle_control_message(self, text: str):
        try:
            message = json.loads(text)
        except json.JSONDecodeError:
            logger.warning(
                "Ignoring malformed Vonage media control message",
                extra={
                    "event": "media_control_invalid",
                    "dispatch_job_id": self.conversation.dispatch_job_id,
                },
            )
            return

        control_event = (
            message.get("event") if isinstance(message, dict) else None
        )
        if not isinstance(control_event, str):
            logger.warning(
                "Ignoring Vonage media control message without an event",
                extra={
                    "event": "media_control_invalid",
                    "dispatch_job_id": self.conversation.dispatch_job_id,
                },
            )
            return

        logger.info(
            "Vonage media control event received",
            extra={
                "event": "media_control_received",
                "dispatch_job_id": self.conversation.dispatch_job_id,
                "control_event": control_event,
            },
        )

    def _create_response_task(self, coroutine, stage: str):
        task = asyncio.create_task(coroutine)
        task.add_done_callback(
            lambda completed: self._log_response_failure(completed, stage)
        )
        return task

    def _log_response_failure(self, task: asyncio.Task, stage: str):
        if task.cancelled():
            return
        error = task.exception()
        if error:
            self.response_error = error
            self.response_failure.set()
            logger.error(
                "Live voice response failed for dispatch job %s",
                self.conversation.dispatch_job_id,
                exc_info=(type(error), error, error.__traceback__),
                extra={
                    "event": "live_voice_response_failed",
                    "dispatch_job_id": self.conversation.dispatch_job_id,
                    "stage": stage,
                },
            )

    async def _greet(self):
        started = time.perf_counter()
        logger.info(
            "Live call initial greeting started",
            extra={
                "event": "voice_greeting_started",
                "dispatch_job_id": self.conversation.dispatch_job_id,
            },
        )
        try:
            _, pcm = await asyncio.wait_for(
                self.conversation.greet(),
                timeout=VOICE_RESPONSE_TIMEOUT_SECONDS,
            )
            await asyncio.wait_for(
                self._play_audio(pcm),
                timeout=VOICE_RESPONSE_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.exception(
                "Live call initial greeting failed",
                extra={
                    "event": "voice_greeting_failed",
                    "dispatch_job_id": self.conversation.dispatch_job_id,
                },
            )
            raise
        self.last_activity = time.monotonic()
        logger.info(
            "Live call initial greeting playback completed",
            extra={
                "event": "voice_greeting_completed",
                "dispatch_job_id": self.conversation.dispatch_job_id,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )

    async def _respond_to_utterance(self, utterance: bytes):
        result = await asyncio.wait_for(
            self.conversation.process_utterance(utterance),
            timeout=VOICE_RESPONSE_TIMEOUT_SECONDS,
        )
        if result is None:
            await self._handle_unrecognized_speech()
            return

        self.unrecognized_count = 0
        _, pcm = result
        await asyncio.wait_for(
            self._play_audio(pcm),
            timeout=VOICE_RESPONSE_TIMEOUT_SECONDS,
        )
        self.last_activity = time.monotonic()
        if getattr(self.conversation, "should_end_call", False):
            await self._end_call("caller_requested_exit")

    async def _handle_unrecognized_speech(self):
        self.unrecognized_count += 1
        if self.unrecognized_count <= self.max_reprompts:
            prompt = (
                "Maaf kijiye, main samajh nahi paayi. Kripya apna sawaal "
                "dobara kahiye, ya call band karne ke liye 'call band' kahiye."
                if getattr(self.conversation, "caller_language", "en") == "hi"
                else "Sorry, I didn't catch that. Please repeat your question, "
                "or say 'call band' to end the call."
            )
            await self._speak(prompt)
            return

        goodbye = (
            "Maaf kijiye, main aapki baat samajh nahi paayi. "
            "Call yahin khatam karte hain. Namaste."
            if getattr(self.conversation, "caller_language", "en") == "hi"
            else "Sorry, I still couldn't understand. I'll end the call now. "
            "Goodbye."
        )
        await self._speak(goodbye)
        await self._end_call("unrecognized_speech")

    async def _speak(self, text: str):
        _, pcm = await asyncio.wait_for(
            self.conversation.synthesize_assistant(text),
            timeout=VOICE_RESPONSE_TIMEOUT_SECONDS,
        )
        await asyncio.wait_for(
            self._play_audio(pcm),
            timeout=VOICE_RESPONSE_TIMEOUT_SECONDS,
        )
        self.last_activity = time.monotonic()

    async def _play_audio(self, pcm: bytes):
        if not pcm or len(pcm) % SAMPLE_WIDTH or not any(pcm):
            raise AudioTranscodeError(
                "Voice playback requires non-silent, aligned PCM audio"
            )

        loop = asyncio.get_running_loop()
        playback_started = loop.time()
        frames_sent = 0
        for offset in range(0, len(pcm), PLAYBACK_FRAME_BYTES):
            frame = pcm[offset:offset + PLAYBACK_FRAME_BYTES]
            if len(frame) % SAMPLE_WIDTH:
                frame += b"\x00"
            if len(frame) < PLAYBACK_FRAME_BYTES:
                frame += bytes(PLAYBACK_FRAME_BYTES - len(frame))
            async with self.send_lock:
                await self.websocket.send_bytes(frame)
            frames_sent += 1
            if offset + PLAYBACK_FRAME_BYTES < len(pcm):
                next_frame_at = (
                    playback_started + frames_sent * PLAYBACK_FRAME_SECONDS
                )
                delay = next_frame_at - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
        logger.info(
            "Live call audio transmitted to Vonage",
            extra={
                "event": "voice_audio_transmitted",
                "dispatch_job_id": self.conversation.dispatch_job_id,
                "audio_bytes": len(pcm),
                "audio_frames": frames_sent,
                "audio_duration_ms": round(
                    len(pcm) * 1000 / (SAMPLE_RATE * SAMPLE_WIDTH),
                    2,
                ),
            },
        )

    async def _interrupt(self):
        active = self.response_task
        if active and not active.done():
            active.cancel()
            await asyncio.gather(active, return_exceptions=True)
        self.response_task = None
        async with self.send_lock:
            await self.websocket.send_text('{"action":"clear"}')

    async def _cancel_response(self):
        active = self.response_task
        if active and not active.done():
            active.cancel()
            await asyncio.gather(active, return_exceptions=True)
        self.response_task = None

    async def _watch_silence(self):
        while self.running:
            await asyncio.sleep(0.5)
            active = self.response_task
            if active and not active.done():
                continue
            if time.monotonic() - self.last_activity < self.silence_timeout:
                continue

            if self.reprompt_count < self.max_reprompts:
                self.reprompt_count += 1
                self.last_activity = time.monotonic()
                prompt = (
                    "Maaf kijiye, kya aap apna sawaal dobara keh sakte hain?"
                    if getattr(self.conversation, "caller_language", "en") == "hi"
                    else "Sorry, I didn't hear you. Could you repeat your question?"
                )
                self.response_task = self._create_response_task(
                    self._speak(prompt),
                    stage="silence_reprompt",
                )
                continue

            await self._hangup_after_silence()
            return

    async def _hangup_after_silence(self):
        goodbye = (
            "Aapka jawab nahi mila, isliye call yahin khatam karte hain. Namaste."
            if getattr(self.conversation, "caller_language", "en") == "hi"
            else "I didn't hear a response, so I'll end the call now. Goodbye."
        )
        await self._speak(goodbye)
        await self._end_call("silence_timeout")

    async def _end_call(self, reason: str):
        await self._hangup_call(reason)
        self.running = False
        try:
            if getattr(self.websocket.client_state, "name", "") == "CONNECTED":
                await self.websocket.close(code=1000)
        except Exception:
            logger.exception("Could not close the silent call WebSocket")

    async def _hangup_call(self, reason: str):
        if not self.call_uuid:
            return
        try:
            hung_up = await asyncio.to_thread(
                self.telephony_provider.hangup_call,
                self.call_uuid,
            )
            if not hung_up:
                logger.error(
                    "Telephony provider did not hang up the call",
                    extra={
                        "event": "voice_call_hangup_failed",
                        "dispatch_job_id": self.conversation.dispatch_job_id,
                        "reason": reason,
                    },
                )
        except Exception:
            logger.exception(
                "Could not hang up the call",
                extra={
                    "event": "voice_call_hangup_error",
                    "dispatch_job_id": self.conversation.dispatch_job_id,
                    "reason": reason,
                },
            )
