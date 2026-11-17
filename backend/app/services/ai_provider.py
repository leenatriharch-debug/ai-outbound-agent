from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class AIMessage:
    role: str
    content: str


@dataclass
class AIResponse:
    content: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


class AIProvider(ABC):

    @abstractmethod
    async def generate_response(
        self,
        messages: list[AIMessage],
        system_prompt: str
    ) -> AIResponse:
        pass