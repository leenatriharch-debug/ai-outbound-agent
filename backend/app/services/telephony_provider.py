from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class CallRequest:
    phone_number: str
    answer_url: str
    event_url: str


@dataclass
class CallResult:
    call_uuid: str
    status: str


class TelephonyProvider(ABC):

    @abstractmethod
    def create_call(self, request: CallRequest) -> CallResult:
        pass

    @abstractmethod
    def transfer_call(
        self,
        call_uuid: str,
        destination_number: str
    ) -> bool:
        pass

    @abstractmethod
    def hangup_call(self, call_uuid: str) -> bool:
        pass