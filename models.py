
from datetime import date
from pydantic import BaseModel


class Patient(BaseModel):
    id: int
    first_name: str
    last_name: str
    date_of_birth: date
    gender: str


class PatientCreate(BaseModel):
    first_name: str
    last_name: str
    date_of_birth: date
    gender: str
