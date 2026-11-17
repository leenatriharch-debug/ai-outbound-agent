import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.database.connection import get_db
from app.models.user_account import UserAccount

bearer_scheme = HTTPBearer(auto_error=False)
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1


@dataclass(frozen=True)
class AuthenticatedUser:
    id: int
    username: str
    role: str


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived_key = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=32,
    )
    salt_text = base64.urlsafe_b64encode(salt).decode("ascii").rstrip("=")
    key_text = base64.urlsafe_b64encode(derived_key).decode("ascii").rstrip("=")
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt_text}${key_text}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        scheme, n_text, r_text, p_text, salt_text, key_text = stored_hash.split("$")
        if (
            scheme != "scrypt"
            or int(n_text) != _SCRYPT_N
            or int(r_text) != _SCRYPT_R
            or int(p_text) != _SCRYPT_P
        ):
            return False
        salt = base64.urlsafe_b64decode(salt_text + "=" * (-len(salt_text) % 4))
        expected = base64.urlsafe_b64decode(key_text + "=" * (-len(key_text) % 4))
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=_SCRYPT_N,
            r=_SCRYPT_R,
            p=_SCRYPT_P,
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


_DUMMY_PASSWORD_HASH = hash_password("invalid-account-password")


def authenticate_user(db: Session, username: str, password: str):
    user = db.query(UserAccount).filter(
        UserAccount.username == username
    ).first()
    password_hash = user.password_hash if user else _DUMMY_PASSWORD_HASH
    valid_password = verify_password(password, password_hash)
    if not user or not user.is_active or not valid_password:
        return None
    return user


def create_access_token(user: UserAccount) -> tuple[str, int]:
    secret = settings.AUTH_JWT_SECRET.get_secret_value()
    if len(secret.encode("utf-8")) < 32:
        raise HTTPException(
            status_code=503,
            detail="Authentication is not configured",
        )

    expires_in = settings.AUTH_ACCESS_TOKEN_MINUTES * 60
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": str(user.id),
            "iat": now,
            "exp": now + timedelta(seconds=expires_in),
            "jti": secrets.token_urlsafe(16),
        },
        secret,
        algorithm="HS256",
    )
    return token, expires_in


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> AuthenticatedUser:
    if settings.APP_ENV.lower() != "production":
        return AuthenticatedUser(id=0, username="local-development", role="admin")

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    secret = settings.AUTH_JWT_SECRET.get_secret_value()
    if len(secret.encode("utf-8")) < 32:
        raise HTTPException(
            status_code=503,
            detail="Authentication is not configured",
        )
    try:
        claims = jwt.decode(
            credentials.credentials,
            secret,
            algorithms=["HS256"],
        )
        user_id = int(claims["sub"])
    except (InvalidTokenError, KeyError, TypeError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from error

    user = db.query(UserAccount).filter(
        UserAccount.id == user_id,
        UserAccount.is_active.is_(True),
    ).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return AuthenticatedUser(
        id=user.id,
        username=user.username,
        role=user.role,
    )


def require_roles(*roles: str):
    def dependency(user: AuthenticatedUser = Depends(get_current_user)):
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return user

    return dependency


def validate_production_settings():
    if settings.APP_ENV.lower() != "production":
        return

    missing = []
    auth_secret = settings.AUTH_JWT_SECRET.get_secret_value()
    if len(auth_secret.encode("utf-8")) < 32:
        missing.append("AUTH_JWT_SECRET (at least 32 bytes)")
    if len(settings.VONAGE_SIGNATURE_SECRET.encode("utf-8")) < 32:
        missing.append("VONAGE_SIGNATURE_SECRET (at least 32 bytes)")
    if urlparse(settings.PUBLIC_BASE_URL).scheme != "https":
        missing.append("PUBLIC_BASE_URL (HTTPS)")
    for name in (
        "VONAGE_API_KEY",
        "VONAGE_APPLICATION_ID",
        "VONAGE_NUMBER",
        "GROQ_API_KEY",
    ):
        if not getattr(settings, name):
            missing.append(name)
    if not Path(settings.VONAGE_PRIVATE_KEY_PATH).is_file():
        missing.append("VONAGE_PRIVATE_KEY_PATH (readable key file)")
    if settings.TRANSCRIPT_RETENTION_DAYS < 1:
        missing.append("TRANSCRIPT_RETENTION_DAYS (positive integer)")
    if settings.AUTH_ACCESS_TOKEN_MINUTES < 1:
        missing.append("AUTH_ACCESS_TOKEN_MINUTES (positive integer)")
    if settings.MAINTENANCE_INTERVAL_SECONDS < 1:
        missing.append("MAINTENANCE_INTERVAL_SECONDS (positive integer)")
    if settings.STALE_CONVERSATION_MINUTES < 1:
        missing.append("STALE_CONVERSATION_MINUTES (positive integer)")

    if missing:
        raise RuntimeError(
            "Invalid production configuration: " + ", ".join(missing)
        )
