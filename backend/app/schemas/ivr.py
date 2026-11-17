from pydantic import BaseModel, ConfigDict, Field


class IVRMenuOptionCreate(BaseModel):
    digit: str = Field(pattern=r"^[0-9]$")
    label: str = Field(min_length=1, max_length=100)
    destination_phone: str = Field(pattern=r"^\+?[1-9]\d{7,14}$")


class IVRMenuOptionResponse(IVRMenuOptionCreate):
    id: int

    model_config = ConfigDict(from_attributes=True)


class IVRMenuCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    welcome_message: str = Field(min_length=1)
    invalid_input_message: str = "Sorry, that wasn't a valid option. Please try again."
    timeout_message: str = "We didn't receive a selection. Goodbye."
    max_attempts: int = Field(default=3, ge=1, le=5)
    timeout_seconds: int = Field(default=10, ge=3, le=60)
    is_active: bool = True
    options: list[IVRMenuOptionCreate] = Field(min_length=1, max_length=10)


class IVRMenuUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    welcome_message: str | None = Field(default=None, min_length=1)
    invalid_input_message: str | None = None
    timeout_message: str | None = None
    max_attempts: int | None = Field(default=None, ge=1, le=5)
    timeout_seconds: int | None = Field(default=None, ge=3, le=60)
    is_active: bool | None = None
    options: list[IVRMenuOptionCreate] | None = Field(
        default=None,
        min_length=1,
        max_length=10
    )


class IVRMenuResponse(BaseModel):
    id: int
    name: str
    welcome_message: str
    invalid_input_message: str | None
    timeout_message: str | None
    max_attempts: int
    timeout_seconds: int
    is_active: bool
    options: list[IVRMenuOptionResponse]

    model_config = ConfigDict(from_attributes=True)
