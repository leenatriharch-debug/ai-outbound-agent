from threading import Lock

from app.config.settings import settings
from app.services.ai_provider import AIProvider
from app.services.edge_tts_provider import EdgeTTSProvider
from app.services.groq_provider import GroqAIProvider
from app.services.provider_errors import ProviderConfigurationError
from app.services.speech_processing_service import SpeechProcessingService
from app.services.speech_provider import (
    SpeechToTextProvider,
    TextToSpeechProvider
)
from app.services.stt import GroqSpeechToTextProvider
from app.services.telephony_provider import TelephonyProvider
from app.services.vonage_provider import VonageTelephonyProvider


class ProviderRegistry:

    def __init__(self, config=settings):
        self._config = config
        self._providers = {}
        self._lock = Lock()

    def _get_provider(self, capability, setting_name, factories):
        provider_name = getattr(self._config, setting_name, "").strip().lower()
        factory = factories.get(provider_name)
        if factory is None:
            supported = ", ".join(sorted(factories))
            raise ProviderConfigurationError(
                f"Unsupported {capability} provider '{provider_name}'. "
                f"Supported providers: {supported}"
            )

        key = (capability, provider_name)
        with self._lock:
            if key not in self._providers:
                self._providers[key] = factory()
            return self._providers[key]

    def get_llm_provider(self) -> AIProvider:
        return self._get_provider(
            "LLM",
            "LLM_PROVIDER",
            {"groq": GroqAIProvider}
        )

    def get_stt_provider(self) -> SpeechToTextProvider:
        return self._get_provider(
            "STT",
            "STT_PROVIDER",
            {"groq": GroqSpeechToTextProvider}
        )

    def get_tts_provider(self) -> TextToSpeechProvider:
        return self._get_provider(
            "TTS",
            "TTS_PROVIDER",
            {"edge_tts": EdgeTTSProvider}
        )

    def get_telephony_provider(self) -> TelephonyProvider:
        return self._get_provider(
            "telephony",
            "TELEPHONY_PROVIDER",
            {"vonage": VonageTelephonyProvider}
        )

    def get_stt_processing_service(self) -> SpeechProcessingService:
        return SpeechProcessingService(stt_provider=self.get_stt_provider())

    def get_tts_processing_service(self) -> SpeechProcessingService:
        return SpeechProcessingService(tts_provider=self.get_tts_provider())

    def get_voice_processing_service(self) -> SpeechProcessingService:
        return SpeechProcessingService(
            stt_provider=self.get_stt_provider(),
            tts_provider=self.get_tts_provider(),
        )

    def reset(self):
        with self._lock:
            self._providers.clear()


_provider_registry = ProviderRegistry()


def get_provider_registry() -> ProviderRegistry:
    return _provider_registry
