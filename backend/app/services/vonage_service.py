from pathlib import Path

import vonage
from vonage_http_client.errors import HttpRequestError
from vonage_voice.models.connect_endpoints import PhoneEndpoint
from vonage_voice.models.ncco import Connect

from app.core.config import settings
from app.services.provider_errors import (
    ProviderCallRejected,
    ProviderConfigurationError,
)


class VonageService:

    def __init__(self, client=None):
        if client is not None:
            self.client = client
            return

        if not settings.VONAGE_APPLICATION_ID or not settings.VONAGE_NUMBER:
            raise ProviderConfigurationError(
                "Vonage application ID and caller number must be configured"
            )

        private_key_path = Path(settings.VONAGE_PRIVATE_KEY_PATH)
        try:
            private_key = private_key_path.read_text(encoding="utf-8")
        except OSError as error:
            raise ProviderConfigurationError(
                "Vonage private key could not be read from the configured path"
            ) from error

        auth = vonage.Auth(
            application_id=settings.VONAGE_APPLICATION_ID,
            private_key=private_key
        )

        self.client = vonage.Vonage(auth)

    def make_call(
        self,
        phone_number: str,
        answer_url: str | None = None,
        event_url: str | None = None
    ):
        base_url = settings.PUBLIC_BASE_URL.rstrip("/")
        if (not answer_url or not event_url) and not base_url:
            raise ProviderConfigurationError(
                "PUBLIC_BASE_URL is required to create a Vonage call"
            )

        caller_number = settings.VONAGE_NUMBER
        try:
            response = self.client.voice.create_call({
                "to": [
                    {
                        "type": "phone",
                        "number": phone_number
                    }
                ],
                "from_": {
                    "type": "phone",
                    "number": caller_number
                },
                "answer_url": [
                    answer_url or f"{base_url}/api/voice/answer"
                ],
                "event_url": [
                    event_url or f"{base_url}/api/voice/events"
                ]
            })
        except HttpRequestError as error:
            status_code = error.response.status_code
            if status_code == 429:
                raise ProviderCallRejected(
                    "Vonage rejected the call request",
                    retryable=True
                ) from error
            if status_code in {400, 401, 403, 404, 422}:
                raise ProviderCallRejected(
                    "Vonage rejected the call request",
                    retryable=False
                ) from error
            raise

        return response

    def transfer_call(self, call_uuid: str, destination_number: str) -> bool:
        connect = Connect(
            endpoint=[
                PhoneEndpoint(number=destination_number.lstrip("+"))
            ],
            from_=settings.VONAGE_NUMBER.lstrip("+") or None
        )
        self.client.voice.transfer_call_ncco(call_uuid, [connect])
        return True

    def hangup_call(self, call_uuid: str) -> bool:
        self.client.voice.hangup(call_uuid)
        return True