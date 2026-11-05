
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
