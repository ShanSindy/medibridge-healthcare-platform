"""
Audit-log retrieval (GET /audit-logs) against real PostgreSQL.

The first test uses a real persisted admin, real sign-in and real patient
mutations to produce events. The ordering and paging tests insert events
directly with explicit timestamps, so that equal timestamps (the id tie-breaker)
can be set up deterministically without sleeps; they override the current user
with that real admin row because authentication is not what they test.
"""

import secrets
from datetime import UTC, datetime

import psycopg
import pytest
from fastapi.testclient import TestClient

import main
from auth_dependencies import get_current_user
from tests import db_safety
from user_service import create_user

FIELDS = ("id", "user_id", "action", "resource_type", "resource_id", "outcome", "created_at")


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
def admin(db):
    db()
    password = secrets.token_urlsafe(24)
    user = create_user(
        username="synthetic.audit.admin",
        email="audit.admin@example.org",
        password=password,
        role="admin",
    )
    return user, password


@pytest.fixture
def overridden_client(admin):
    user, _ = admin
    previous = dict(main.app.dependency_overrides)
    main.app.dependency_overrides[get_current_user] = lambda: {
        key: user[key] for key in ("id", "username", "email", "role", "is_active")
    }
    try:
        yield TestClient(main.app)
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous)


def stored_events(db):
    rows = (
        db()
        .execute(
            "SELECT id, user_id, action, resource_type, resource_id, outcome, created_at "
            "FROM audit_logs"
        )
        .fetchall()
    )
    return {row[0]: dict(zip(FIELDS, row, strict=True)) for row in rows}


def test_admin_retrieves_events_produced_by_real_patient_changes(db, admin):
    user, password = admin
    previous = dict(main.app.dependency_overrides)
    main.app.dependency_overrides.clear()  # real sign-in and authorization
    try:
        client = TestClient(main.app)
        login = client.post(
            "/auth/login", data={"username": user["username"], "password": password}
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        patient = {
            "first_name": "Rowan",
            "last_name": "Ledger",
            "date_of_birth": "1990-02-16",
            "gender": "female",
        }
        first = client.post("/patients", json=patient, headers=headers).json()["id"]
        second = client.post("/patients", json=patient, headers=headers).json()["id"]
        assert client.delete(f"/patients/{first}", headers=headers).status_code == 204

        response = client.get("/audit-logs", headers=headers)
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous)

    assert response.status_code == 200
    body = response.json()
    assert (body["limit"], body["offset"]) == (50, 0)
    items = body["items"]
    assert [(i["action"], i["resource_id"]) for i in items] == [
        ("patient.delete", str(first)),
        ("patient.create", str(second)),
        ("patient.create", str(first)),
    ]
    stored = stored_events(db)
    for item in items:
        assert set(item) == set(FIELDS)
        row = stored[item["id"]]
        assert item["user_id"] == row["user_id"] == user["id"]
        assert (item["resource_type"], item["outcome"]) == ("patient", "success")
        assert datetime.fromisoformat(item["created_at"]) == row["created_at"]


@pytest.fixture
def timed_events(db, admin):
    """Five events: two share one timestamp, so their order depends on the id tie-breaker."""
    user, _ = admin
    conn = db()
    times = [
        datetime(2031, 1, 1, 9, 0, tzinfo=UTC),
        datetime(2031, 1, 1, 12, 0, tzinfo=UTC),
        datetime(2031, 1, 1, 12, 0, tzinfo=UTC),
        datetime(2031, 1, 1, 8, 0, tzinfo=UTC),
        datetime(2031, 1, 1, 10, 0, tzinfo=UTC),
    ]
    ids = []
    for number, created_at in enumerate(times, start=1):
        ids.append(
            conn.execute(
                "INSERT INTO audit_logs (user_id, action, resource_type, resource_id, outcome, "
                "created_at) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    user["id"],
                    "patient.update",
                    "patient",
                    f"synthetic-{number}",
                    "success",
                    created_at,
                ),
            ).fetchone()[0]
        )
    # created_at DESC, then id DESC for the two events at 12:00.
    return [ids[2], ids[1], ids[4], ids[0], ids[3]]


def test_events_are_ordered_newest_first_with_id_as_tie_breaker(overridden_client, timed_events):
    items = overridden_client.get("/audit-logs").json()["items"]

    assert [item["id"] for item in items] == timed_events


@pytest.mark.parametrize(
    ("limit", "offset", "expected"),
    [(2, 0, slice(0, 2)), (2, 2, slice(2, 4)), (2, 4, slice(4, 5)), (2, 5, slice(5, 5))],
    ids=["first-page", "second-page", "last-partial-page", "past-the-end"],
)
def test_limit_and_offset_page_through_the_ordered_events(
    overridden_client, timed_events, limit, offset, expected
):
    response = overridden_client.get("/audit-logs", params={"limit": limit, "offset": offset})

    assert response.status_code == 200
    body = response.json()
    assert (body["limit"], body["offset"]) == (limit, offset)
    assert [item["id"] for item in body["items"]] == timed_events[expected]


def test_largest_allowed_page_returns_every_event(overridden_client, timed_events):
    response = overridden_client.get("/audit-logs", params={"limit": 100})

    assert [item["id"] for item in response.json()["items"]] == timed_events
