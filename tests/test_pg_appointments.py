"""
Appointment scheduling against real PostgreSQL.

Requests go through the real appointment routes, services and database. The
overlap rules come from migration 005:

    EXCLUDE USING gist (provider_id WITH =, tstzrange(starts_at, ends_at, '[)') WITH &&)
    WHERE (status = 'scheduled')

Each test checks the HTTP response and the committed rows, read through a
separate connection to this session's disposable database. Authentication is
not under test here: the current user is overridden with a synthetic admin.
"""

from datetime import datetime

import psycopg
import pytest
from fastapi.testclient import TestClient

import main
from auth_dependencies import get_current_user
from tests import db_safety

CONFLICT = "This provider already has a scheduled appointment during the requested time."
DAY = "2031-03-10"


# --- helpers -----------------------------------------------------------------------


@pytest.fixture
def db(disposable_database):
    """Opens connections only to this session's disposable database, verified by identity."""
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
        "username": "synthetic.scheduler.admin",
        "email": "synthetic.admin@example.org",
        "role": "admin",
        "is_active": True,
    }
    try:
        yield TestClient(main.app)
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous)


@pytest.fixture
def seed(db):
    """Creates one synthetic patient and returns a function that creates providers."""
    conn = db()
    patient_id = conn.execute(
        "INSERT INTO patients (first_name, last_name, date_of_birth, gender) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        ("Avery", "Schedule", "1985-03-14", "female"),
    ).fetchone()[0]

    def provider(last_name="Slot"):
        return conn.execute(
            "INSERT INTO providers (first_name, last_name, specialty) "
            "VALUES (%s, %s, %s) RETURNING id",
            ("Jordan", last_name, "Cardiology"),
        ).fetchone()[0]

    return patient_id, provider


def at(time, offset="-06:00"):
    return f"{DAY}T{time}{offset}"


def book(client, patient_id, provider_id, start, end):
    return client.post(
        "/appointments",
        json={
            "patient_id": patient_id,
            "provider_id": provider_id,
            "starts_at": start,
            "ends_at": end,
            "reason": "Synthetic scheduling check",
        },
    )


def stored(db, provider_id):
    """Committed appointments for a provider: (id, starts_at, ends_at, status), by id."""
    return (
        db()
        .execute(
            "SELECT id, starts_at, ends_at, status FROM appointments "
            "WHERE provider_id = %s ORDER BY id",
            (provider_id,),
        )
        .fetchall()
    )


def instant(text):
    return datetime.fromisoformat(text)


def assert_conflict(response):
    assert response.status_code == 409
    assert response.json() == {"detail": CONFLICT}


# --- overlap rules -------------------------------------------------------------------


def test_identical_interval_for_the_same_provider_is_rejected(client, db, seed):
    patient_id, provider = seed
    doctor = provider()
    first = book(client, patient_id, doctor, at("09:00:00"), at("10:00:00"))
    assert first.status_code == 201
    original = stored(db, doctor)

    assert_conflict(book(client, patient_id, doctor, at("09:00:00"), at("10:00:00")))

    assert stored(db, doctor) == original
    assert [row[0] for row in original] == [first.json()["id"]]
    # The rejected insert left nothing behind that blocks a valid booking.
    assert book(client, patient_id, doctor, at("11:00:00"), at("11:30:00")).status_code == 201


@pytest.mark.parametrize(
    ("start", "end"),
    [("08:30:00", "09:30:00"), ("09:30:00", "10:30:00")],
    ids=["overlaps-start", "overlaps-end"],
)
def test_partial_overlap_is_rejected(client, db, seed, start, end):
    patient_id, provider = seed
    doctor = provider()
    assert book(client, patient_id, doctor, at("09:00:00"), at("10:00:00")).status_code == 201
    original = stored(db, doctor)

    assert_conflict(book(client, patient_id, doctor, at(start), at(end)))
    assert stored(db, doctor) == original


@pytest.mark.parametrize(
    ("start", "end"),
    [("08:00:00", "11:00:00"), ("09:15:00", "09:45:00")],
    ids=["contains-existing", "inside-existing"],
)
def test_containing_and_contained_intervals_are_rejected(client, db, seed, start, end):
    patient_id, provider = seed
    doctor = provider()
    assert book(client, patient_id, doctor, at("09:00:00"), at("10:00:00")).status_code == 201
    original = stored(db, doctor)

    assert_conflict(book(client, patient_id, doctor, at(start), at(end)))
    assert stored(db, doctor) == original


def test_back_to_back_appointments_are_both_accepted(client, db, seed):
    """Fails if the range bounds stop being half-open ('[)')."""
    patient_id, provider = seed
    doctor = provider()
    first = book(client, patient_id, doctor, at("09:00:00"), at("10:00:00"))
    second = book(client, patient_id, doctor, at("10:00:00"), at("11:00:00"))

    assert (first.status_code, second.status_code) == (201, 201)
    rows = stored(db, doctor)
    assert [(r[0], r[3]) for r in rows] == [
        (first.json()["id"], "scheduled"),
        (second.json()["id"], "scheduled"),
    ]
    assert rows[0][2] == rows[1][1] == instant(at("10:00:00"))


def test_same_interval_for_different_providers_is_accepted(client, db, seed):
    """Fails if the provider dimension is removed from the exclusion constraint."""
    patient_id, provider = seed
    first_doctor, second_doctor = provider("One"), provider("Two")

    for doctor in (first_doctor, second_doctor):
        assert book(client, patient_id, doctor, at("09:00:00"), at("10:00:00")).status_code == 201
    assert len(stored(db, first_doctor)) == len(stored(db, second_doctor)) == 1


def test_overlapping_instants_written_with_other_utc_offsets_conflict(client, db, seed):
    patient_id, provider = seed
    doctor = provider()
    assert book(client, patient_id, doctor, at("09:00:00"), at("10:00:00")).status_code == 201
    original = stored(db, doctor)

    # 15:30Z-16:30Z is 09:30-10:30 at -06:00, so it overlaps the existing booking.
    assert_conflict(book(client, patient_id, doctor, at("15:30:00", "Z"), at("16:30:00", "Z")))
    assert stored(db, doctor) == original
    # The stored instant is independent of the offset used to write it.
    assert original[0][1] == instant(at("15:00:00", "+00:00"))


# --- status-dependent rules ------------------------------------------------------------


def test_cancelled_slot_can_be_rebooked(client, db, seed):
    patient_id, provider = seed
    doctor = provider()
    first = book(client, patient_id, doctor, at("09:00:00"), at("10:00:00")).json()["id"]

    cancelled = client.patch(f"/appointments/{first}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    rebooked = book(client, patient_id, doctor, at("09:00:00"), at("10:00:00"))
    assert rebooked.status_code == 201
    assert [(r[0], r[3]) for r in stored(db, doctor)] == [
        (first, "cancelled"),
        (rebooked.json()["id"], "scheduled"),
    ]


def test_reactivating_a_cancelled_appointment_into_a_rebooked_slot_is_rejected(client, db, seed):
    patient_id, provider = seed
    doctor = provider()
    first = book(client, patient_id, doctor, at("09:00:00"), at("10:00:00")).json()["id"]
    assert client.patch(f"/appointments/{first}/cancel").status_code == 200
    assert book(client, patient_id, doctor, at("09:00:00"), at("10:00:00")).status_code == 201
    before = stored(db, doctor)

    response = client.put(
        f"/appointments/{first}",
        json={"starts_at": at("09:00:00"), "ends_at": at("10:00:00"), "status": "scheduled"},
    )

    assert_conflict(response)
    assert stored(db, doctor) == before
    assert before[0][3] == "cancelled"


def test_completed_appointment_does_not_block_a_new_booking(client, db, seed):
    """
    Characterises the current database rule (only 'scheduled' rows are exclusive);
    it is not a statement of clinical scheduling policy.
    """
    patient_id, provider = seed
    doctor = provider()
    first = book(client, patient_id, doctor, at("09:00:00"), at("10:00:00")).json()["id"]
    completed = client.put(
        f"/appointments/{first}",
        json={"starts_at": at("09:00:00"), "ends_at": at("10:00:00"), "status": "completed"},
    )
    assert completed.status_code == 200

    second = book(client, patient_id, doctor, at("09:00:00"), at("10:00:00"))

    assert second.status_code == 201
    assert [(r[0], r[3]) for r in stored(db, doctor)] == [
        (first, "completed"),
        (second.json()["id"], "scheduled"),
    ]


# --- updates -------------------------------------------------------------------------


def test_update_into_an_occupied_interval_is_rejected_without_partial_change(client, db, seed):
    patient_id, provider = seed
    doctor = provider()
    morning = book(client, patient_id, doctor, at("09:00:00"), at("10:00:00")).json()["id"]
    afternoon = book(client, patient_id, doctor, at("14:00:00"), at("15:00:00")).json()["id"]
    before = stored(db, doctor)

    response = client.put(
        f"/appointments/{afternoon}",
        json={
            "starts_at": at("09:30:00"),
            "ends_at": at("10:30:00"),
            "status": "scheduled",
            "reason": "Moved into an occupied slot",
        },
    )

    assert_conflict(response)
    assert stored(db, doctor) == before
    reason = db().execute("SELECT reason FROM appointments WHERE id = %s", (afternoon,))
    assert reason.fetchone()[0] == "Synthetic scheduling check"

    moved = client.put(
        f"/appointments/{afternoon}",
        json={"starts_at": at("10:00:00"), "ends_at": at("11:00:00"), "status": "scheduled"},
    )
    assert moved.status_code == 200
    assert stored(db, doctor)[1][1:3] == (instant(at("10:00:00")), instant(at("11:00:00")))
    assert stored(db, doctor)[0] == before[0]
    assert morning == before[0][0]


def test_update_and_cancellation_persist(client, db, seed):
    patient_id, provider = seed
    doctor = provider()
    created = book(client, patient_id, doctor, at("09:00:00"), at("10:00:00")).json()
    appointment_id = created["id"]

    fetched = client.get(f"/appointments/{appointment_id}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == appointment_id
    assert instant(fetched.json()["starts_at"]) == instant(at("09:00:00"))

    updated = client.put(
        f"/appointments/{appointment_id}",
        json={
            "starts_at": at("13:00:00"),
            "ends_at": at("13:45:00"),
            "status": "scheduled",
            "reason": "Synthetic follow-up",
        },
    )
    assert updated.status_code == 200
    row = (
        db()
        .execute(
            "SELECT starts_at, ends_at, status, reason, patient_id, provider_id "
            "FROM appointments WHERE id = %s",
            (appointment_id,),
        )
        .fetchone()
    )
    assert row == (
        instant(at("13:00:00")),
        instant(at("13:45:00")),
        "scheduled",
        "Synthetic follow-up",
        patient_id,
        doctor,
    )

    cancelled = client.patch(f"/appointments/{appointment_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    status = db().execute("SELECT status FROM appointments WHERE id = %s", (appointment_id,))
    assert status.fetchone()[0] == "cancelled"

    listed = client.get("/appointments", params={"provider_id": doctor, "status": "cancelled"})
    assert [a["id"] for a in listed.json()] == [appointment_id]


def test_unknown_appointment_ids_return_404_without_changes(client, db, seed):
    patient_id, provider = seed
    doctor = provider()
    existing = book(client, patient_id, doctor, at("09:00:00"), at("10:00:00")).json()["id"]
    missing = existing + 1000
    before = stored(db, doctor)

    for response in (
        client.get(f"/appointments/{missing}"),
        client.put(
            f"/appointments/{missing}",
            json={"starts_at": at("12:00:00"), "ends_at": at("13:00:00"), "status": "scheduled"},
        ),
        client.patch(f"/appointments/{missing}/cancel"),
    ):
        assert response.status_code == 404
        assert response.json() == {"detail": "Appointment not found"}
    assert stored(db, doctor) == before
