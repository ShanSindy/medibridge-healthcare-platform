"""Service information and liveness endpoints."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/")
def home():
    return {"application": "MediBridge", "status": "running"}


@router.get("/health")
def health_check():
    return {"status": "healthy", "service": "MediBridge API"}
