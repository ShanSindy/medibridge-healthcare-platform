"""
Patient handlers and their audit events against real PostgreSQL.

The real POST/PUT/DELETE /patients handlers run against the disposable database,
and committed state is read through a separate, identity-checked connection.
The acting user is a real row created with `user_service.create_user` (audit
events reference users.id); the current-user dependency is overridden to that
row because authentication itself is not under test here.

Fault injection (the only non-real element, used in the rollback tests): the
handler's `log_audit_event` collaborator is wrapped so that it calls the real
function on the handler's own connection, confirms the audit row is visible
inside that transaction, and then raises a sentinel exception before the
transaction can commit. Connections, SQL, commits and rollbacks are not mocked.
"""

import secrets

import psycopg
import pytest
from fastapi.testclient import TestClient

import main
import routers.patients
from auth_dependencies import get_current_user
from tests import db_safety
from user_service import create_user

PATIENT = {
    "first_name": "Morgan",
    "last_name": "Auditrail",
    "date_of_birth": "1972-07-09",
    "gender": "male",
}


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
def actor(db):
    db()  # proves the session database before any write
    user = create_user(
        username="synthetic.clinician.audit",
        email="clinician.audit@example.org",
        password=secrets.token_urlsafe(24),
        role="clinician",
    )
    previous = dict(main.app.dependency_overrides)
    main.app.dependency_overrides[get_current_user] = lambda: {
        key: user[key] for key in ("id", "username", "email", "role", "is_active")
    }
    try:
        yield user
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous)


@pytest.fixture
def client(actor):
    return TestClient(main.app)


def audit_rows(db, resource_id=None):
    query = "SELECT action, resource_type, resource_id, user_id, outcome, details FROM audit_logs"
    params = ()
    if resource_id is not None:
        query += " WHERE resource_id = %s"
        params = (str(resource_id),)
    return db().execute(query + " ORDER BY id", params).fetchall()


def patient_row(db, patient_id):
    return (
        db()
        .execute(
            "SELECT first_name, last_name, date_of_birth::text, gender FROM patients WHERE id = %s",
            (patient_id,),
        )
        .fetchone()
    )


def create_patient(client):
    response = client.post("/patients", json=PATIENT)
    assert response.status_code == 201
    return response.json()["id"]


# --- successful mutations ---------------------------------------------------------------


def test_create_commits_the_patient_and_its_audit_event(client, db, actor):
    patient_id = create_patient(client)

    assert patient_row(db, patient_id) == ("Morgan", "Auditrail", "1972-07-09", "male")
    assert audit_rows(db) == [
        ("patient.create", "patient", str(patient_id), actor["id"], "success", {"source": "api"})
    ]
    created_at = db().execute("SELECT created_at FROM audit_logs").fetchone()[0]
    assert created_at is not None


def test_update_commits_the_change_and_its_audit_event(client, db, actor):
    patient_id = create_patient(client)

    response = client.put(f"/patients/{patient_id}", json={**PATIENT, "first_name": "Morgana"})

    assert response.status_code == 200
    assert response.json()["first_name"] == "Morgana"
    assert patient_row(db, patient_id)[0] == "Morgana"
    assert [row[0] for row in audit_rows(db, patient_id)] == ["patient.create", "patient.update"]
    assert audit_rows(db, patient_id)[1] == (
        "patient.update",
        "patient",
        str(patient_id),
        actor["id"],
        "success",
        None,
    )


def test_delete_removes_the_patient_and_keeps_a_retrievable_audit_event(db, actor):
    client = TestClient(main.app)
    patient_id = create_patient(client)

    assert client.delete(f"/patients/{patient_id}").status_code == 204

    assert patient_row(db, patient_id) is None
    assert audit_rows(db, patient_id)[-1] == (
        "patient.delete",
        "patient",
        str(patient_id),
        actor["id"],
        "success",
        None,
    )
    # The audit trail outlives the patient and is returned by the audit endpoint.
    main.app.dependency_overrides[get_current_user] = lambda: {
        "id": actor["id"],
        "username": actor["username"],
        "email": actor["email"],
        "role": "admin",
        "is_active": True,
    }
    items = client.get("/audit-logs").json()["items"]
    assert items[0]["action"] == "patient.delete"
    assert items[0]["resource_id"] == str(patient_id)
    assert items[0]["user_id"] == actor["id"]


# --- rejected mutations -------------------------------------------------------------------


def test_rejected_delete_and_update_write_no_audit_event(client, db):
    patient_id = create_patient(client)
    conn = db()
    provider_id = conn.execute(
        "INSERT INTO providers (first_name, last_name, specialty) VALUES (%s, %s, %s) RETURNING id",
        ("Sam", "Auditor", "Pediatrics"),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO appointments (patient_id, provider_id, starts_at, ends_at) "
        "VALUES (%s, %s, %s, %s)",
        (patient_id, provider_id, "2031-05-01T09:00:00Z", "2031-05-01T09:30:00Z"),
    )

    conflict = client.delete(f"/patients/{patient_id}")
    assert conflict.status_code == 409
    assert conflict.json() == {"detail": "Patient has appointments and cannot be deleted."}
    assert patient_row(db, patient_id) is not None

    missing = patient_id + 1000
    not_found = client.put(f"/patients/{missing}", json=PATIENT)
    assert not_found.status_code == 404
    assert not_found.json() == {"detail": "Patient not found"}

    assert [row[0] for row in audit_rows(db)] == ["patient.create"]
    assert audit_rows(db, missing) == []


# --- rollback with an injected failure after the real audit insert -------------------------


class InjectedAuditFailure(RuntimeError):
    """Raised on purpose after the real audit row has been written."""


def inject_failure_after_audit(monkeypatch):
    real_log_audit_event = routers.patients.log_audit_event
    observed = {}

    def failing_log_audit_event(**kwargs):
        real_log_audit_event(**kwargs)
        conn = kwargs["connection"]
        observed["audit_rows_inside_transaction"] = conn.execute(
            "SELECT count(*) FROM audit_logs WHERE action = %s AND resource_id = %s",
            (kwargs["action"], kwargs["resource_id"]),
        ).fetchone()[0]
        raise InjectedAuditFailure

    monkeypatch.setattr(routers.patients, "log_audit_event", failing_log_audit_event)
    return observed


@pytest.mark.parametrize("operation", ["create", "update", "delete"])
def test_failure_after_audit_rolls_back_the_patient_change_and_the_audit(
    client, db, monkeypatch, operation
):
    patient_id = create_patient(client) if operation != "create" else None
    before = audit_rows(db)
    failing = TestClient(main.app, raise_server_exceptions=False)
    observed = inject_failure_after_audit(monkeypatch)

    if operation == "create":
        response = failing.post("/patients", json={**PATIENT, "last_name": "Rollback"})
    elif operation == "update":
        response = failing.put(f"/patients/{patient_id}", json={**PATIENT, "first_name": "X"})
    else:
        response = failing.delete(f"/patients/{patient_id}")

    assert response.status_code == 500
    assert observed["audit_rows_inside_transaction"] == 1  # the real insert did happen
    assert audit_rows(db) == before  # ...and was rolled back
    if operation == "create":
        count = db().execute("SELECT count(*) FROM patients WHERE last_name = 'Rollback'")
        assert count.fetchone()[0] == 0
    else:
        assert patient_row(db, patient_id) == ("Morgan", "Auditrail", "1972-07-09", "male")

    monkeypatch.undo()  # remove the injected failure; normal writes work again
    restored = client.put(
        f"/patients/{patient_id or create_patient(client)}",
        json={**PATIENT, "first_name": "Recovered"},
    )
    assert restored.status_code == 200
    assert audit_rows(db)[-1][0] == "patient.update"
