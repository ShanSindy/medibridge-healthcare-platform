
from models import Patient
from fastapi import FastAPI, HTTPException
from patients import patients

app = FastAPI(
    title="MediBridge",
    description="Healthcare Integration & Interoperability Platform",
    version="0.1.0"
)

@app.get("/")
def home():
    return {
        "application": "MediBridge",
        "status": "running"
    }

@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": "MediBridge API"
    }

@app.get("/patients", response_model=list[Patient])
def get_patients():
    return patients

@app.get("/patients/{patient_id}", response_model=Patient)
def get_patient(patient_id: int):
    for patient in patients:
        if patient["id"] == patient_id:
            return patient

    raise HTTPException(
        status_code=404,
        detail="Patient not found"
    )
