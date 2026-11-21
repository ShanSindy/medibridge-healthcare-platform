"""
Provider endpoints against real PostgreSQL.

Requests use the real provider routes, service and database; committed state is
read through a separate, identity-checked connection. Authentication is not
under test: the current user is overridden with a synthetic admin. Exact-case
duplicate emails and referenced-provider deletes are already covered in
test_integrity_errors.py and are not repeated here.
"""

import psycopg
import pytest
from fastapi.testclient import TestClient

import main
from auth_dependencies import get_current_user
from tests import db_safety

DUPLICATE_EMAIL = "A provider with this email already exists."


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
def client():
    previous = dict(main.app.dependency_overrides)
    main.app.dependency_overrides[get_current_user] = lambda: {
        "id": None,
        "username": "synthetic.provider.admin",
        "email": "provider.admin@example.org",
        "role": "admin",
        "is_active": True,
    }
    try:
        yield TestClient(main.app)
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous)


def stored(db, provider_id):
    return (
        db()
        .execute(
            "SELECT first_name, last_name, specialty, email, phone FROM providers WHERE id = %s",
            (provider_id,),
        )
        .fetchone()
    )


def provider_count(db):
    return db().execute("SELECT count(*) FROM providers").fetchone()[0]


def test_provider_crud_round_trip_persists_every_change(client, db):
    created = client.post(
        "/providers",
        json={
            "first_name": "  Elena ",
            "last_name": "Vasquez",
            "specialty": "Endocrinology",
            "email": "  Elena.Vasquez@Example.ORG ",
            "phone": "555-0150",
        },
    )
    assert created.status_code == 201
    provider_id = created.json()["id"]
    # Names are trimmed and the email trimmed and lower-cased by the request model.
    assert stored(db, provider_id) == (
        "Elena",
        "Vasquez",
        "Endocrinology",
        "elena.vasquez@example.org",
        "555-0150",
    )
    assert created.json()["email"] == "elena.vasquez@example.org"

    listed = client.get("/providers")
    assert [p["id"] for p in listed.json()] == [provider_id]
    fetched = client.get(f"/providers/{provider_id}")
    assert fetched.status_code == 200
    assert fetched.json()["specialty"] == "Endocrinology"

    updated = client.put(
        f"/providers/{provider_id}",
        json={
            "first_name": "Elena",
            "last_name": "Vasquez",
            "specialty": "Internal Medicine",
            "email": "elena.vasquez@example.org",
            "phone": None,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["id"] == provider_id
    assert stored(db, provider_id) == (
        "Elena",
        "Vasquez",
        "Internal Medicine",
        "elena.vasquez@example.org",
        None,
    )

    assert client.delete(f"/providers/{provider_id}").status_code == 204
    assert stored(db, provider_id) is None
    gone = client.get(f"/providers/{provider_id}")
    assert gone.status_code == 404
    assert gone.json() == {"detail": "Provider not found"}


def test_duplicate_email_differing_only_in_case_is_rejected(client, db):
    original = client.post(
        "/providers",
        json={
            "first_name": "Taylor",
            "last_name": "Ward",
            "specialty": "Dermatology",
            "email": "taylor.ward@example.org",
        },
    ).json()
    other = client.post(
        "/providers",
        json={"first_name": "Robin", "last_name": "Hale", "specialty": "Dermatology"},
    ).json()
    before = (stored(db, original["id"]), stored(db, other["id"]), provider_count(db))

    on_create = client.post(
        "/providers",
        json={
            "first_name": "Tay",
            "last_name": "Ward",
            "specialty": "Oncology",
            "email": "Taylor.Ward@EXAMPLE.org",
        },
    )
    on_update = client.put(
        f"/providers/{other['id']}",
        json={
            "first_name": "Robin",
            "last_name": "Hale",
            "specialty": "Dermatology",
            "email": "TAYLOR.WARD@example.ORG",
        },
    )

    for response in (on_create, on_update):
        assert response.status_code == 409
        assert response.json() == {"detail": DUPLICATE_EMAIL}
    assert (stored(db, original["id"]), stored(db, other["id"]), provider_count(db)) == before

    valid = client.put(
        f"/providers/{other['id']}",
        json={
            "first_name": "Robin",
            "last_name": "Hale",
            "specialty": "Dermatology",
            "email": "robin.hale@example.org",
        },
    )
    assert valid.status_code == 200
    assert stored(db, other["id"])[3] == "robin.hale@example.org"
