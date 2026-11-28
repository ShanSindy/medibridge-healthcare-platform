"""Provider directory endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError

from auth_dependencies import require_permission
from db_errors import raise_for_constraint
from models import Provider, ProviderCreate
from provider_service import (
    create_provider,
    delete_provider,
    get_all_providers,
    get_provider_by_id,
    update_provider,
)

router = APIRouter()

# Constraint violations that are client errors, per operation. The service writes on
# the shared engine, so they arrive as SQLAlchemy IntegrityError (the psycopg error in
# `.orig`); raise_for_constraint maps only the constraints listed here and re-raises
# anything else, so unexpected database errors are not masked. The delete mapping
# covers ON DELETE RESTRICT (the current schema) and ON DELETE NO ACTION alike: both
# name the same foreign key constraint.
PROVIDER_DELETE_CONSTRAINTS = {
    "appointments_provider_id_fkey": (
        409,
        "Provider has appointments and cannot be deleted.",
    ),
}
PROVIDER_WRITE_CONSTRAINTS = {
    "providers_email_key": (
        409,
        "A provider with this email already exists.",
    ),
}


@router.post(
    "/providers",
    response_model=Provider,
    status_code=201,
    tags=["Providers"],
    dependencies=[Depends(require_permission("providers:write"))],
)
def create_provider_endpoint(provider: ProviderCreate):
    """
    Create a new healthcare provider.
    """
    try:
        return create_provider(provider)
    except IntegrityError as exc:
        raise_for_constraint(exc, PROVIDER_WRITE_CONSTRAINTS)


@router.get(
    "/providers",
    response_model=list[Provider],
    tags=["Providers"],
    dependencies=[Depends(require_permission("providers:read"))],
)
def list_providers_endpoint():
    """
    Retrieve all healthcare providers.
    """
    return get_all_providers()


@router.get(
    "/providers/{provider_id}",
    response_model=Provider,
    tags=["Providers"],
    dependencies=[Depends(require_permission("providers:read"))],
)
def get_provider_endpoint(provider_id: int):
    """
    Retrieve a healthcare provider by ID.
    """
    provider = get_provider_by_id(provider_id)

    if provider is None:
        raise HTTPException(
            status_code=404,
            detail="Provider not found",
        )

    return provider


@router.put(
    "/providers/{provider_id}",
    response_model=Provider,
    tags=["Providers"],
    dependencies=[Depends(require_permission("providers:write"))],
)
def update_provider_endpoint(
    provider_id: int,
    provider: ProviderCreate,
):
    """
    Update an existing healthcare provider.
    """
    try:
        updated_provider = update_provider(provider_id, provider)
    except IntegrityError as exc:
        raise_for_constraint(exc, PROVIDER_WRITE_CONSTRAINTS)

    if updated_provider is None:
        raise HTTPException(
            status_code=404,
            detail="Provider not found",
        )

    return updated_provider


@router.delete(
    "/providers/{provider_id}",
    status_code=204,
    tags=["Providers"],
    dependencies=[Depends(require_permission("providers:write"))],
)
def delete_provider_endpoint(provider_id: int):
    """
    Delete a healthcare provider by ID.
    """
    try:
        deleted = delete_provider(provider_id)
    except IntegrityError as exc:
        raise_for_constraint(exc, PROVIDER_DELETE_CONSTRAINTS)

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Provider not found",
        )

    return None
