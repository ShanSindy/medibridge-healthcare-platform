
from typing import Literal
from datetime import date
from pydantic import BaseModel, Field, field_validator


class Patient(BaseModel):
    id: int
    first_name: str
    last_name: str
    date_of_birth: date
    gender: str


class PatientCreate(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    date_of_birth: date
    gender: Literal["male", "female", "other", "unknown"]

    @field_validator("first_name", "last_name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("Name cannot be empty or whitespace")

        return value

    @field_validator("date_of_birth")
    @classmethod
    def validate_date_of_birth(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("Date of birth cannot be in the future")

        return value

from pydantic import EmailStr, Field, field_validator
from typing import Optional
from datetime import datetime


class ProviderCreate(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    specialty: str = Field(min_length=1, max_length=150)
    email: Optional[str] = Field(default=None, max_length=255)
    phone: Optional[str] = Field(default=None, max_length=30)

    @field_validator("first_name", "last_name", "specialty")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("Field cannot be empty or whitespace.")

        return value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None

        value = value.strip()

        if not value:
            return None

        if "@" not in value or "." not in value.split("@")[-1]:
            raise ValueError("Invalid email address.")

        return value.lower()


class Provider(ProviderCreate):
    id: int
    created_at: datetime
