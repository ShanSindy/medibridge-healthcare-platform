"""
Authentication and users against real PostgreSQL, with no dependency overrides.

Users are created with `user_service.create_user`, sign in through the real
form-encoded POST /auth/login, and use the returned bearer token. Every request
goes through HTTPBearer, token verification and the per-request user lookup.
Passwords are random per test and never printed.
"""

import secrets

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import errors

import main
from security import verify_password
from tests import db_safety
from user_service import create_user


@pytest.fixture
def db(disposable_database):
    opened = []

    def connect():
        conn = psycopg.connect(
            **disposable_database.config.connect_kwargs(disposable_database.name),
            autocommit=True,
        )
        opened.append(conn)
        found, role_oid = db_safety.fetch_identity(conn, disposable_database.name)
        db_safety.check_identity(
            session_name=disposable_database.name,
            created=disposable_database.created,
            found=found,
            current_role_oid=role_oid,
            require_marker=True,
        )
        return conn

    yield connect
    for conn in opened:
        conn.close()


@pytest.fixture
def client(db):
    db()  # proves the session database before any user is written
    previous = dict(main.app.dependency_overrides)
    main.app.dependency_overrides.clear()  # authentication must be real in this module
    try:
        yield TestClient(main.app)
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous)


def new_user(role, name):
    password = secrets.token_urlsafe(24)
    user = create_user(
        username=f"synthetic.{name}",
        email=f"{name}@example.org",
        password=password,
        role=role,
    )
    return user, password


def login(client, username, password):
    return client.post("/auth/login", data={"username": username, "password": password})


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def token_for(client, user, password):
    response = login(client, user["username"], password)
    assert response.status_code == 200
    return response.json()["access_token"]


def stored_user(db, user_id):
    return (
        db()
        .execute(
            "SELECT username, email, role, is_active, password_hash FROM users WHERE id = %s",
            (user_id,),
        )
        .fetchone()
    )


def test_created_user_is_stored_with_an_argon2id_hash_and_can_sign_in(client, db):
    user, password = new_user("clinician", "login.clinician")

    username, email, role, is_active, password_hash = stored_user(db, user["id"])
    assert (username, email, role, is_active) == (
        "synthetic.login.clinician",
        "login.clinician@example.org",
        "clinician",
        True,
    )
    assert password not in password_hash
    assert password_hash.startswith("$argon2id$")
    assert verify_password(password, password_hash)
    assert not verify_password(password + "-wrong", password_hash)

    response = login(client, user["username"], password)
    assert response.status_code == 200
    body = response.json()
    assert (body["token_type"], body["expires_in"]) == ("bearer", 1800)

    me = client.get("/auth/me", headers=bearer(body["access_token"]))
    assert me.status_code == 200
    assert me.json() == {
        "id": user["id"],
        "username": "synthetic.login.clinician",
        "email": "login.clinician@example.org",
        "role": "clinician",
        "is_active": True,
    }


def test_wrong_password_is_rejected(client):
    user, password = new_user("viewer", "wrong.password")

    response = login(client, user["username"], password + "-wrong")

    assert response.status_code == 401
    assert response.json() == {"detail": "Incorrect username or password"}
    assert response.headers["www-authenticate"] == "Bearer"


def test_inactive_user_cannot_sign_in(client, db):
    user, password = new_user("clinician", "inactive.clinician")
    db().execute("UPDATE users SET is_active = false WHERE id = %s", (user["id"],))

    response = login(client, user["username"], password)

    assert response.status_code == 401
    assert response.json() == {"detail": "Incorrect username or password"}


def test_deactivating_a_user_stops_an_already_issued_token(client, db):
    """Checks the per-request active-user lookup; there is no token revocation list."""
    user, password = new_user("scheduler", "deactivated.scheduler")
    token = token_for(client, user, password)
    assert client.get("/auth/me", headers=bearer(token)).status_code == 200

    db().execute("UPDATE users SET is_active = false WHERE id = %s", (user["id"],))
    response = client.get("/auth/me", headers=bearer(token))

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or missing authentication credentials"}


def test_persisted_roles_are_enforced(client, db):
    clinician, clinician_password = new_user("clinician", "role.clinician")
    scheduler, scheduler_password = new_user("scheduler", "role.scheduler")
    clinician_token = token_for(client, clinician, clinician_password)
    scheduler_token = token_for(client, scheduler, scheduler_password)
    patient = {
        "first_name": "Quinn",
        "last_name": "Roles",
        "date_of_birth": "1995-01-12",
        "gender": "female",
    }

    forbidden = client.post("/patients", json=patient, headers=bearer(scheduler_token))
    assert forbidden.status_code == 403
    assert forbidden.json() == {"detail": "Insufficient permissions"}
    assert db().execute("SELECT count(*) FROM patients").fetchone()[0] == 0

    allowed = client.post("/patients", json=patient, headers=bearer(clinician_token))
    assert allowed.status_code == 201
    audit_user = db().execute("SELECT user_id FROM audit_logs").fetchone()[0]
    assert audit_user == clinician["id"]
    # Schedulers may read patients.
    listed = client.get("/patients", headers=bearer(scheduler_token))
    assert [p["id"] for p in listed.json()] == [allowed.json()["id"]]


@pytest.mark.parametrize(
    ("username", "email", "constraint"),
    [
        ("synthetic.unique", "different@example.org", "users_username_key"),
        ("synthetic.different", "unique@example.org", "users_email_key"),
    ],
    ids=["username", "email"],
)
def test_usernames_and_emails_are_unique(db, username, email, constraint):
    db()
    original = create_user(
        username="synthetic.unique",
        email="unique@example.org",
        password=secrets.token_urlsafe(24),
        role="viewer",
    )
    before = stored_user(db, original["id"])

    with pytest.raises(errors.UniqueViolation) as raised:
        create_user(
            username=username, email=email, password=secrets.token_urlsafe(24), role="viewer"
        )

    assert raised.value.sqlstate == "23505"
    assert raised.value.diag.constraint_name == constraint
    assert db().execute("SELECT count(*) FROM users").fetchone()[0] == 1
    assert stored_user(db, original["id"]) == before


def test_unknown_roles_are_rejected_by_the_database(db):
    """create_user does not validate roles itself; the users_valid_role check does."""
    db()
    with pytest.raises(errors.CheckViolation) as raised:
        create_user(
            username="synthetic.superadmin",
            email="superadmin@example.org",
            password=secrets.token_urlsafe(24),
            role="superadmin",
        )

    assert raised.value.sqlstate == "23514"
    assert raised.value.diag.constraint_name == "users_valid_role"
    assert db().execute("SELECT count(*) FROM users").fetchone()[0] == 0
    # A valid user can still be created afterwards.
    assert new_user("viewer", "after.check")[0]["role"] == "viewer"
