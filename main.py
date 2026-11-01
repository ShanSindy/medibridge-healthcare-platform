
from fastapi import FastAPI

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
