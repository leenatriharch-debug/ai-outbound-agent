import hashlib
import hmac
import time
from urllib.parse import urlencode, urlparse

import jwt
from fastapi import Request, WebSocket
from jwt import InvalidTokenError

from app.config.settings import settings


class WebhookVerificationError(RuntimeError):

    def __init__(self, message: str, status_code: int = 401):
        super().__init__(message)
        self.status_code = status_code


def verify_vonage_webhook(
    request: Request | WebSocket,
    raw_body: bytes = b"",
) -> dict:
    if not settings.VONAGE_SIGNATURE_SECRET or not settings.VONAGE_API_KEY:
        raise WebhookVerificationError(
            "Vonage webhook verification is not configured",
            status_code=503
        )
    if len(settings.VONAGE_SIGNATURE_SECRET.encode("utf-8")) < 32:
        raise WebhookVerificationError(
            "VONAGE_SIGNATURE_SECRET must be at least 32 bytes",
            status_code=503
        )

    authorization = request.headers.get("authorization", "")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        raise WebhookVerificationError("Missing Vonage webhook authorization")

    try:
        claims = jwt.decode(
            token.strip(),
            settings.VONAGE_SIGNATURE_SECRET,
            algorithms=["HS256"],
            options={
                "verify_exp": False,
                "verify_iat": False,
                "verify_aud": False,
            },
        )
    except InvalidTokenError as error:
        raise WebhookVerificationError(
            "Invalid Vonage webhook signature"
        ) from error

    issued_at = claims.get("iat")
    if (
        claims.get("iss") != "Vonage"
        or claims.get("api_key") != settings.VONAGE_API_KEY
        or not isinstance(claims.get("jti"), str)
        or not claims["jti"]
        or len(claims["jti"]) > 100
        or not isinstance(issued_at, int)
        or issued_at > int(time.time()) + 300
        or issued_at < int(time.time()) - 86400
    ):
        raise WebhookVerificationError("Invalid Vonage webhook claims")

    payload_hash = claims.get("payload_hash")
    if payload_hash and raw_body:
        actual_hash = hashlib.sha256(raw_body).hexdigest()
        if not hmac.compare_digest(str(payload_hash).lower(), actual_hash):
            raise WebhookVerificationError(
                "Vonage webhook payload hash does not match"
            )

    return claims


def build_callback_urls(
    base_url: str,
    dispatch_job_id: int,
    signature_secret: str
) -> tuple[str, str]:
    if len(signature_secret.encode("utf-8")) < 32:
        raise WebhookVerificationError(
            "VONAGE_SIGNATURE_SECRET must be at least 32 bytes",
            status_code=503
        )
    parsed_base_url = urlparse(base_url)
    if parsed_base_url.scheme != "https" or not parsed_base_url.netloc:
        raise WebhookVerificationError(
            "PUBLIC_BASE_URL must be an HTTPS URL",
            status_code=503
        )

    def callback_url(path: str, purpose: str) -> str:
        message = f"outbound:{purpose}:{dispatch_job_id}".encode("utf-8")
        signature = hmac.new(
            signature_secret.encode("utf-8"),
            message,
            hashlib.sha256
        ).hexdigest()
        query = urlencode({
            "dispatch_id": dispatch_job_id,
            "dispatch_sig": signature
        })
        return f"{base_url.rstrip('/')}{path}?{query}"

    return (
        callback_url("/api/voice/answer", "answer"),
        callback_url("/api/voice/events", "events"),
    )


def build_media_websocket_url(
    base_url: str,
    dispatch_job_id: int,
    signature_secret: str,
) -> str:
    if len(signature_secret.encode("utf-8")) < 32:
        raise WebhookVerificationError(
            "VONAGE_SIGNATURE_SECRET must be at least 32 bytes",
            status_code=503
        )
    parsed_base_url = urlparse(base_url)
    if (
        parsed_base_url.scheme != "https"
        or not parsed_base_url.netloc
        or parsed_base_url.username
        or parsed_base_url.password
    ):
        raise WebhookVerificationError(
            "PUBLIC_BASE_URL must be an HTTPS URL",
            status_code=503
        )

    message = f"outbound:media:{dispatch_job_id}".encode("utf-8")
    signature = hmac.new(
        signature_secret.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()
    query = urlencode({
        "dispatch_id": dispatch_job_id,
        "dispatch_sig": signature,
    })
    path = f"{parsed_base_url.path.rstrip('/')}/api/voice/media"
    return f"wss://{parsed_base_url.netloc}{path}?{query}"


def verify_dispatch_callback(
    dispatch_job_id: int,
    signature: str,
    purpose: str
) -> bool:
    if not settings.VONAGE_SIGNATURE_SECRET or dispatch_job_id < 1:
        return False

    message = f"outbound:{purpose}:{dispatch_job_id}".encode("utf-8")
    expected = hmac.new(
        settings.VONAGE_SIGNATURE_SECRET.encode("utf-8"),
        message,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
