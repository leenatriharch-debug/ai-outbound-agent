from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr


class Settings(BaseSettings):
    APP_NAME: str = "AI Outbound Calling Agent"
    APP_ENV: str = "development"

    DB_HOST: str = "localhost"
    DB_PORT: int = 3306
    DB_USER: str = "root"
    DB_PASSWORD: str
    DB_NAME: str = "ai_outbound_agent"

    VONAGE_API_KEY: str = ""
    VONAGE_API_SECRET: str = ""
    VONAGE_SIGNATURE_SECRET: str = ""
    VONAGE_APPLICATION_ID: str = ""
    VONAGE_PRIVATE_KEY_PATH: str = "private.key"
    VONAGE_NUMBER: str = ""
    PUBLIC_BASE_URL: str = ""

    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.1-8b-instant"
    GROQ_STT_MODEL: str = "whisper-large-v3-turbo"

    LLM_PROVIDER: str = "groq"
    STT_PROVIDER: str = "groq"
    TTS_PROVIDER: str = "edge_tts"
    TELEPHONY_PROVIDER: str = "vonage"
    EDGE_TTS_VOICE: str = "en-US-AriaNeural"
    CALL_GREETING: str = (
        "Hello! This is Worldotech's AI assistant. How can I help you today?"
    )
    CALL_SILENCE_TIMEOUT_SECONDS: int = 15
    CALL_SILENCE_REPROMPTS: int = 1
    CALL_VAD_THRESHOLD: int = 500
    CALL_VAD_SILENCE_MS: int = 700
    CALL_MAX_UTTERANCE_SECONDS: int = 15
    AUTH_JWT_SECRET: SecretStr = SecretStr("")
    AUTH_ACCESS_TOKEN_MINUTES: int = 30
    BOOTSTRAP_ADMIN_USERNAME: str = ""
    BOOTSTRAP_ADMIN_PASSWORD: SecretStr = SecretStr("")
    TRANSCRIPT_RETENTION_DAYS: int = 90
    STALE_CONVERSATION_MINUTES: int = 150
    MAINTENANCE_INTERVAL_SECONDS: int = 3600
    MAX_AUDIO_UPLOAD_BYTES: int = 10485760
    CALL_MAX_AUDIO_FRAME_BYTES: int = 16384

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore"
    )


settings = Settings()