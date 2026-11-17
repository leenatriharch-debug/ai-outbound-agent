from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict, EmailStr


class CustomerCreate(BaseModel):
    full_name: str | None = Field(default=None, max_length=150)
    phone_number: str = Field(min_length=10, max_length=20)
    email: EmailStr | None = None


class CustomerUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=150)
    phone_number: str | None = Field(default=None, min_length=10, max_length=20)
    email: EmailStr | None = None
    is_active: bool | None = None


class CustomerResponse(BaseModel):
    id: int
    full_name: str | None
    phone_number: str
    email: str | None
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)