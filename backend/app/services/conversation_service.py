from sqlalchemy.orm import Session

from app.services.conversation_manager import ConversationManager
from app.services.prompt_manager import PromptManager
from app.services.ai_provider import AIProvider, AIMessage


class ConversationService:

    def __init__(self, ai_provider: AIProvider):
        self.ai_provider = ai_provider

    async def process_message(
        self,
        db: Session,
        session_id: int,
        customer_message: str,
        company_name: str = "Worldotech",
        purpose: str = "customer outreach"
    ):
        if not customer_message.strip():
            raise ValueError("Customer message cannot be empty")

        history = ConversationManager.get_conversation_history(
            db, session_id
        )

        ai_messages = [
            AIMessage(role=message.role, content=message.message)
            for message in history
        ]

        ai_messages.append(
            AIMessage(role="user", content=customer_message)
        )

        prompt = PromptManager.get_outbound_call_prompt(
            company_name, purpose
        )

        response = await self.ai_provider.generate_response(
            messages=ai_messages,
            system_prompt=prompt
        )

        ConversationManager.add_message(
            db, session_id, "user", customer_message
        )

        ConversationManager.add_message(
            db, session_id, "assistant", response.content
        )

        return {
            "session_id": session_id,
            "customer_message": customer_message,
            "assistant_response": response.content,
            "model": response.model
        }