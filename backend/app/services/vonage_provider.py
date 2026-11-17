from app.services.telephony_provider import (
    CallRequest,
    CallResult,
    TelephonyProvider
)
from app.services.vonage_service import VonageService


class VonageTelephonyProvider(TelephonyProvider):

    def __init__(self, service: VonageService | None = None):
        self.service = service or VonageService()

    def create_call(self, request: CallRequest) -> CallResult:
        response = self.service.make_call(
            phone_number=request.phone_number,
            answer_url=request.answer_url,
            event_url=request.event_url
        )
        return CallResult(
            call_uuid=response.uuid,
            status=response.status
        )

    def transfer_call(
        self,
        call_uuid: str,
        destination_number: str
    ) -> bool:
        return self.service.transfer_call(call_uuid, destination_number)

    def hangup_call(self, call_uuid: str) -> bool:
        return self.service.hangup_call(call_uuid)
