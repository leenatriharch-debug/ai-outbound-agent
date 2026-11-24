import json
import unittest

import httpx

from app.services.ai_provider import AIMessage
from app.services.rag_chat_service import RAGAPIError, RAGChatService


class RAGChatServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_posts_latest_message_and_only_last_six_user_assistant_turns(self):
        requests = []

        def handle_request(request):
            requests.append(request)
            return httpx.Response(200, json={"answer": "  RAG answer  "})

        history = [
            AIMessage(role="system", content="do not send"),
            *[
                AIMessage(role="user", content=f"question {index}")
                for index in range(7)
            ],
            *[
                AIMessage(role="assistant", content=f"answer {index}")
                for index in range(7)
            ],
        ]
        transport = httpx.MockTransport(handle_request)
        async with httpx.AsyncClient(transport=transport) as client:
            service = RAGChatService(
                api_url="http://rag.local/",
                api_key="test-key",
                client=client,
            )
            answer = await service.get_answer(
                "current caller question",
                history=history,
            )

        self.assertEqual(answer, "RAG answer")
        self.assertEqual(requests[0].url, "http://rag.local/api/voice/chat")
        self.assertEqual(requests[0].headers["X-API-Key"], "test-key")
        payload = json.loads(requests[0].content)
        self.assertEqual(payload["message"], "current caller question")
        self.assertEqual(len(payload["history"]), 12)
        self.assertTrue(
            all(item["role"] in {"user", "assistant"} for item in payload["history"])
        )

    async def test_omits_empty_history(self):
        requests = []
        transport = httpx.MockTransport(
            lambda request: (
                requests.append(request)
                or httpx.Response(200, json={"answer": "Hi"})
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            service = RAGChatService(
                api_url="http://rag.local",
                api_key="test-key",
                client=client,
            )
            await service.get_answer("Hello")

        self.assertEqual(
            json.loads(requests[0].content),
            {"message": "Hello"},
        )

    async def test_non_success_status_is_reported_without_retry(self):
        requests = []

        def handle_request(request):
            requests.append(request)
            return httpx.Response(503, json={"detail": "private backend error"})

        transport = httpx.MockTransport(handle_request)
        async with httpx.AsyncClient(transport=transport) as client:
            service = RAGChatService(
                api_url="http://rag.local",
                api_key="test-key",
                client=client,
            )
            with self.assertRaises(RAGAPIError) as raised:
                await service.get_answer("question")

        self.assertEqual(raised.exception.reason, "http_error")
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(len(requests), 1)
        self.assertNotIn("private backend error", str(raised.exception))

    async def test_auth_failure_does_not_expose_backend_response(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                401,
                json={"detail": "the API key is invalid"},
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            service = RAGChatService(
                api_url="http://rag.local",
                api_key="test-key",
                client=client,
            )
            with self.assertRaises(RAGAPIError) as raised:
                await service.get_answer("question")

        self.assertEqual(raised.exception.reason, "http_error")
        self.assertNotIn("API key", str(raised.exception))

    async def test_invalid_json_and_missing_answer_are_rejected(self):
        for response in (
            httpx.Response(200, content=b"not json"),
            httpx.Response(200, json={"result": "not the answer field"}),
            httpx.Response(200, json={"answer": "  "}),
        ):
            def handle_request(request, current_response=response):
                return current_response

            transport = httpx.MockTransport(handle_request)
            async with httpx.AsyncClient(transport=transport) as client:
                service = RAGChatService(
                    api_url="http://rag.local",
                    api_key="test-key",
                    client=client,
                )
                with self.assertRaises(RAGAPIError):
                    await service.get_answer("question")

    async def test_timeout_is_reported_without_retry(self):
        requests = []

        def handle_request(request):
            requests.append(request)
            raise httpx.ReadTimeout("request details should not be exposed")

        transport = httpx.MockTransport(handle_request)
        async with httpx.AsyncClient(transport=transport) as client:
            service = RAGChatService(
                api_url="http://rag.local",
                api_key="test-key",
                client=client,
            )
            with self.assertRaises(RAGAPIError) as raised:
                await service.get_answer("question")

        self.assertEqual(raised.exception.reason, "timeout")
        self.assertEqual(len(requests), 1)

    async def test_network_error_is_reported_without_retry(self):
        requests = []

        def handle_request(request):
            requests.append(request)
            raise httpx.ConnectError("internal connection detail")

        transport = httpx.MockTransport(handle_request)
        async with httpx.AsyncClient(transport=transport) as client:
            service = RAGChatService(
                api_url="http://rag.local",
                api_key="test-key",
                client=client,
            )
            with self.assertRaises(RAGAPIError) as raised:
                await service.get_answer("question")

        self.assertEqual(raised.exception.reason, "network_error")
        self.assertEqual(len(requests), 1)
