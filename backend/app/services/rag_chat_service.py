from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.services.ai_provider import AIMessage

RAG_API_TIMEOUT_SECONDS = 12.0
MAX_HISTORY_MESSAGES = 12


class RAGAPIError(RuntimeError):
    def __init__(self, reason: str, status_code: int | None = None):
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


class RAGChatService:
    def __init__(
        self,
        api_url: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float = RAG_API_TIMEOUT_SECONDS,
        client: httpx.AsyncClient | None = None,
    ):
        self.api_url = (api_url if api_url is not None else settings.RAG_API_URL)
        self.api_url = self.api_url.rstrip("/")
        self.api_key = (
            api_key
            if api_key is not None
            else settings.RAG_API_KEY.get_secret_value()
        )
        self.timeout_seconds = timeout_seconds
        self.client = client

    async def get_answer(
        self,
        message: str,
        history: list[AIMessage] | None = None,
    ) -> str:
        if not message.strip():
            raise ValueError("RAG message cannot be empty")
        parsed_url = urlparse(self.api_url)
        if (
            parsed_url.scheme not in {"http", "https"}
            or not parsed_url.netloc
            or parsed_url.username
            or parsed_url.password
            or parsed_url.query
            or parsed_url.fragment
            or not self.api_key
        ):
            raise RAGAPIError("not_configured")

        payload: dict[str, object] = {"message": message}
        safe_history = [
            {"role": item.role, "content": item.content}
            for item in (history or [])
            if item.role in {"user", "assistant"}
        ][-MAX_HISTORY_MESSAGES:]
        if safe_history:
            payload["history"] = safe_history

        try:
            if self.client is not None:
                response = await self.client.post(
                    f"{self.api_url}/api/voice/chat",
                    json=payload,
                    headers={"X-API-Key": self.api_key},
                )
            else:
                async with httpx.AsyncClient(
                    timeout=self.timeout_seconds
                ) as client:
                    response = await client.post(
                        f"{self.api_url}/api/voice/chat",
                        json=payload,
                        headers={"X-API-Key": self.api_key},
                    )
        except httpx.TimeoutException as error:
            raise RAGAPIError("timeout") from error
        except httpx.HTTPError as error:
            raise RAGAPIError("network_error") from error

        if not response.is_success:
            raise RAGAPIError(
                "http_error",
                status_code=response.status_code,
            )

        try:
            response_data = response.json()
        except ValueError as error:
            raise RAGAPIError("invalid_json") from error

        if not isinstance(response_data, dict):
            raise RAGAPIError("invalid_response")
        answer = response_data.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            raise RAGAPIError("invalid_response")
        return answer.strip()
