from app.database.connection import Base, engine
from app.models import (
    Call,
    Customer,
    Campaign,
    CampaignContact,
    CallAttempt,
    ConversationSession,
    ConversationMessage,
    CallDispatchJob,
    VonageWebhookEvent,
    ConversationAttemptLink,
    UserAccount
)


def create_tables():
    Base.metadata.create_all(bind=engine)
    print("AI Outbound Agent database tables created successfully")


if __name__ == "__main__":
    create_tables()