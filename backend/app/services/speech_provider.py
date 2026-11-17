from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class SpeechRecognitionResult:
    text: str
    language: str | None = None
    confidence: float | None = None


class SpeechToTextProvider(ABC):

    @abstractmethod
    async def transcribe(
        self,
        audio_data: bytes,
        filename: str = "audio.wav"
    ) -> SpeechRecognitionResult:
        pass


class TextToSpeechProvider(ABC):

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        language: str,
        voice: str | None = None
    ) -> bytes:
        pass