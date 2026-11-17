from sqlalchemy.orm import Session

from app.services.ai_provider import AIProvider
from app.services.conversation_service import ConversationService


class AgentOrchestrator:

    def __init__(self, ai_provider: AIProvider):
        self.conversation_service = ConversationService(ai_provider)

    async def handle_customer_message(
        self,
        db: Session,
        session_id: int,
        customer_message: str,
        company_name: str = "Worldotech",
        purpose: str = "customer outreach"
    ):
        return await self.conversation_service.process_message(
            db=db,
            session_id=session_id,
            customer_message=customer_message,
            company_name=company_name,
            purpose=purpose
        )