import pytest
from fastapi.testclient import TestClient

import main
from auth_dependencies import get_current_user


client = TestClient(main.app)


@pytest.fixture(autouse=True)
def reset_auth_overrides():
    original = main.app.dependency_overrides.copy()
    main.app.dependency_overrides.clear()

    yield

    main.app.dependency_overrides.clear()
    main.app.dependency_overrides.update(original)


def test_hl7_requires_authentication():
    response = client.post(
        "/hl7/adt",
        content="test",
        headers={"Content-Type": "text/plain"},
    )

    assert response.status_code == 401


@pytest.mark.parametrize("role", ["viewer", "scheduler"])
def test_unauthorized_roles_cannot_send_hl7(role):
    main.app.dependency_overrides[get_current_user] = lambda: {
        "id": 1,
        "username": f"test_{role}",
        "email": f"{role}@example.com",
        "role": role,
        "is_active": True,
    }

    response = client.post(
        "/hl7/adt",
        content="test",
        headers={"Content-Type": "text/plain"},
    )

    assert response.status_code == 403


@pytest.mark.parametrize("role", ["admin", "clinician"])
def test_authorized_roles_reach_hl7_validation(role):
    main.app.dependency_overrides[get_current_user] = lambda: {
        "id": 2,
        "username": f"test_{role}",
        "email": f"{role}@example.com",
        "role": role,
        "is_active": True,
    }

    response = client.post(
        "/hl7/adt",
        content="test",
        headers={"Content-Type": "application/json"},
    )

    # 415 confirms authorization succeeded and the request
    # reached the existing Content-Type validation.
    assert response.status_code == 415
    assert response.json()["detail"] == ("Content-Type must be text/plain.")
