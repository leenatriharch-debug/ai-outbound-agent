import edge_tts

from app.core.config import settings
from app.services.provider_errors import ProviderConfigurationError
from app.services.speech_provider import TextToSpeechProvider


class EdgeTTSProvider(TextToSpeechProvider):

    async def synthesize(
        self,
        text: str,
        language: str,
        voice: str | None = None
    ) -> bytes:
        if voice:
            selected_voice = voice
        elif language.lower().startswith("hi"):
            selected_voice = settings.EDGE_TTS_HI_VOICE
        else:
            selected_voice = settings.EDGE_TTS_VOICE
        if not selected_voice:
            raise ProviderConfigurationError(
                "An Edge TTS voice must be configured"
            )

        communicate = edge_tts.Communicate(
            text=text,
            voice=selected_voice,
            rate="+0%",
            volume="+0%"
        )
        audio = bytearray()

        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                audio.extend(chunk["data"])

        if not audio:
            raise RuntimeError("The TTS provider returned no audio")

        return bytes(audio)
