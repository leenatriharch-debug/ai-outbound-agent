from openai import OpenAI

from app.config.settings import settings


class AIService:

    def __init__(self):
        self.client = OpenAI(
            api_key=settings.AI_API_KEY,
            base_url="https://openrouter.ai/api/v1"
        )

    def generate_response(self, user_message: str):
        response = self.client.chat.completions.create(
            model=settings.AI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "You are a friendly and professional AI calling assistant. Keep your responses natural, clear and conversational."
                },
                {
                    "role": "user",
                    "content": user_message
                }
            ],
            temperature=0.7
        )

        return response.choices[0].message.content