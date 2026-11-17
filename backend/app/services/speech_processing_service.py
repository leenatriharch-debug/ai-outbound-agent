from app.services.speech_provider import (
    SpeechToTextProvider,
    TextToSpeechProvider
)


class SpeechProcessingService:

    def __init__(
        self,
        stt_provider: SpeechToTextProvider | None = None,
        tts_provider: TextToSpeechProvider | None = None
    ):
        self.stt_provider = stt_provider
        self.tts_provider = tts_provider

    async def convert_audio_to_text(
        self,
        audio_data: bytes,
        filename: str = "audio.wav"
    ):
        if not audio_data:
            raise ValueError("Audio data cannot be empty")
        if self.stt_provider is None:
            raise RuntimeError("Speech-to-text provider is not configured")

        return await self.stt_provider.transcribe(audio_data, filename)

    async def convert_text_to_audio(
        self,
        text: str,
        language: str = "en",
        voice: str | None = None
    ):
        if not text.strip():
            raise ValueError("Text cannot be empty")
        if self.tts_provider is None:
            raise RuntimeError("Text-to-speech provider is not configured")

        return await self.tts_provider.synthesize(
            text=text,
            language=language,
            voice=voice
        )