from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_complete_login_and_authenticated_request(monkeypatch):
    """A user can log in and access a protected endpoint."""

    test_user = {
        "id": 1,
        "username": "demo_user",
        "email": "demo@example.com",
        "role": "viewer",
        "is_active": True,
    }

    def fake_authenticate(username, password):
        if username == "demo_user" and password == "CorrectPassword123!":
            return test_user

        return None

    def fake_get_user_by_id(user_id):
        if user_id == 1:
            return test_user

        return None

    monkeypatch.setattr(
        "main.authenticate_user",
        fake_authenticate,
    )

    monkeypatch.setattr(
        "auth_dependencies.get_user_by_id",
        fake_get_user_by_id,
    )

    # Step 1: Log in with valid credentials.
    login_response = client.post(
        "/auth/login",
        data={
            "username": "demo_user",
            "password": "CorrectPassword123!",
        },
    )

    assert login_response.status_code == 200

    access_token = login_response.json()["access_token"]

    # Step 2: Use the JWT to access a protected endpoint.
    profile_response = client.get(
        "/auth/me",
        headers={
            "Authorization": f"Bearer {access_token}",
        },
    )

    assert profile_response.status_code == 200

    profile = profile_response.json()

    assert profile["id"] == 1
    assert profile["username"] == "demo_user"
    assert profile["email"] == "demo@example.com"
    assert profile["role"] == "viewer"
    assert profile["is_active"] is True
    assert "password_hash" not in profile


def test_login_token_rejected_after_account_disabled(monkeypatch):
    """Disabling an account invalidates its access to protected routes."""

    test_user = {
        "id": 1,
        "username": "demo_user",
        "email": "demo@example.com",
        "role": "viewer",
        "is_active": True,
    }

    monkeypatch.setattr(
        "main.authenticate_user",
        lambda username, password: test_user,
    )

    monkeypatch.setattr(
        "auth_dependencies.get_user_by_id",
        lambda user_id: test_user,
    )

    login_response = client.post(
        "/auth/login",
        data={
            "username": "demo_user",
            "password": "CorrectPassword123!",
        },
    )

    assert login_response.status_code == 200

    access_token = login_response.json()["access_token"]

    # Simulate an administrator disabling the account.
    test_user["is_active"] = False

    profile_response = client.get(
        "/auth/me",
        headers={
            "Authorization": f"Bearer {access_token}",
        },
    )

    assert profile_response.status_code == 401
