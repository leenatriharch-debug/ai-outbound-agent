from groq import AsyncGroq
from app.config.settings import settings
from app.services.ai_provider import AIProvider, AIMessage, AIResponse
from app.services.provider_errors import ProviderConfigurationError


class GroqAIProvider(AIProvider):

    def __init__(self):
        if not settings.GROQ_API_KEY:
            raise ProviderConfigurationError(
                "Groq API key is not configured"
            )

        self.client = AsyncGroq(
            api_key=settings.GROQ_API_KEY
        )
        self.model = settings.GROQ_MODEL

    async def generate_response(
        self,
        messages: list[AIMessage],
        system_prompt: str
    ) -> AIResponse:

        chat_messages = [
            {
                "role": "system",
                "content": system_prompt
            }
        ]

        for message in messages:
            chat_messages.append({
                "role": message.role,
                "content": message.content
            })

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=chat_messages,
            temperature=0.5,
            max_completion_tokens=300
        )

        return AIResponse(
            content=response.choices[0].message.content or "",
            model=response.model,
            input_tokens=response.usage.prompt_tokens,
            output_tokens=response.usage.completion_tokens
        )