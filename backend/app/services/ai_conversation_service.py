from app.services.ai_provider import AIMessage, AIProvider


class AIConversationService:

    def __init__(self, provider: AIProvider):
        self.provider = provider

    async def generate_response(
        self,
        user_message: str,
        history: list[AIMessage] | None = None,
    ) -> str:
        response = await self.provider.generate_response(
            messages=[
                *(history or []),
                AIMessage(role="user", content=user_message),
            ],
            system_prompt=(
                "You are Worldotech's AI calling assistant. "
                "Handle incoming and outgoing customer calls. "
                "Help with Sales, Support, Accounts and demo bookings. "
                "Keep replies short and natural for voice conversations."
            )
        )
        return response.content.strip()    
    
    