from groq import AsyncGroq

from app.config.settings import settings
from app.services.provider_errors import ProviderConfigurationError
from app.services.speech_provider import (
    SpeechRecognitionResult,
    SpeechToTextProvider
)


class GroqSpeechToTextProvider(SpeechToTextProvider):

    def __init__(self, client: AsyncGroq | None = None):
        if client is None and not settings.GROQ_API_KEY:
            raise ProviderConfigurationError(
                "Groq API key is not configured"
            )
        self.client = client or AsyncGroq(api_key=settings.GROQ_API_KEY)

    async def transcribe(
        self,
        audio_data: bytes,
        filename: str = "audio.wav"
    ) -> SpeechRecognitionResult:
        result = await self.client.audio.transcriptions.create(
            file=(filename, audio_data),
            model=settings.GROQ_STT_MODEL,
            response_format="text"
        )

        return SpeechRecognitionResult(
            text=str(result).strip(),
            language=None
        )


STTService = GroqSpeechToTextProvider
