from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


UserRole = Literal["admin", "operator"]


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserCreate(BaseModel):
    username: str = Field(
        min_length=3,
        max_length=50,
        pattern=r"^[A-Za-z0-9_.-]+$",
    )
    password: str = Field(min_length=12, max_length=128)
    role: UserRole = "operator"


class UserUpdate(BaseModel):
    password: str | None = Field(default=None, min_length=12, max_length=128)
    role: UserRole | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def require_update(self):
        if self.password is None and self.role is None and self.is_active is None:
            raise ValueError("At least one account field must be provided")
        return self


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: UserRole
    is_active: bool
    created_at: datetime | None
