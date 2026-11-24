"""
MediBridge API entry point (`main:app`).

Creates the FastAPI application, registers the FHIR-aware request
validation error handler, and includes the API routers. Route handlers
live in the routers package.

The lifespan does nothing at startup: no database connection, engine,
migration or seeding. At shutdown it closes the shared database engine's
idle connections, if the engine was ever created (see db_engine.py).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

import db_engine
from fhir import create_operation_outcome
from routers import appointments, audit_logs, auth, hl7, patients, providers, system
from routers import fhir as fhir_routes


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    try:
        yield
    finally:
        # Uvicorn runs this after it stops accepting connections and has waited for open
        # requests. It does not run at all on a forced exit (a second Ctrl+C), and work
        # in threadpool threads can outlive a graceful-shutdown timeout; connections left
        # in those cases end with the process. Never creates an engine; a no-op if no
        # request used one.
        db_engine.dispose_engine()


app = FastAPI(lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def fhir_validation_exception_handler(request: Request, exc: RequestValidationError):
    if request.url.path.startswith("/fhir/"):
        return JSONResponse(
            status_code=422,
            content=create_operation_outcome(
                code="invalid", diagnostics="Invalid FHIR request parameters"
            ),
            media_type="application/fhir+json",
        )

    return await request_validation_exception_handler(request, exc)


# Routers are included in this order, which is also the order of the
# operations in the OpenAPI document.
app.include_router(system.router)
app.include_router(audit_logs.router)
app.include_router(patients.router)
app.include_router(fhir_routes.router)
app.include_router(hl7.router)
app.include_router(providers.router)
app.include_router(appointments.router)
app.include_router(auth.router)
