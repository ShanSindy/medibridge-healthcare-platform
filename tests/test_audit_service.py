"""
audit_service.get_audit_logs on the shared SQLAlchemy engine (db_engine), against this
session's disposable database.

Expected results come from the pre-Phase 2 query run directly through psycopg on a
separate, identity-checked connection, so the tests show that moving the read onto the
engine changed neither the records, their order, their key order nor their value types.
"""

import os
from datetime import UTC, datetime
from unittest.mock import patch

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy import exc as sa_exc

import audit_service
import db_engine
import main
from audit_service import get_audit_logs
from auth_dependencies import get_current_user
from tests import db_safety

FIELDS = ["id", "user_id", "action", "resource_type", "resource_id", "outcome", "created_at"]

# The query as it was before Phase 2 (psycopg placeholders).
DIRECT_QUERY = """
    SELECT id, user_id, action, resource_type, resource_id, outcome, created_at
    FROM audit_logs
    ORDER BY created_at DESC, id DESC
    LIMIT %s OFFSET %s
"""


@pytest.fixture
def direct(disposable_database):
    """An autocommit psycopg connection, verified to be this session's database."""
    conn = psycopg.connect(
        **disposable_database.config.connect_kwargs(disposable_database.name), autocommit=True
    )
    try:
        found, role_oid = db_safety.fetch_identity(conn, disposable_database.name)
        db_safety.check_identity(
            session_name=disposable_database.name,
            created=disposable_database.created,
            found=found,
            current_role_oid=role_oid,
            require_marker=True,
        )
        yield conn
    finally:
        conn.close()


def insert_event(conn, created_at, resource_id, details=None) -> int:
    return conn.execute(
        "INSERT INTO audit_logs (user_id, action, resource_type, resource_id, outcome, "
        "details, created_at) VALUES (NULL, 'patient.update', 'patient', %s, 'success', %s, %s) "
        "RETURNING id",
        (resource_id, psycopg.types.json.Jsonb(details) if details else None, created_at),
    ).fetchone()[0]


def direct_page(conn, limit, offset) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(DIRECT_QUERY, (limit, offset))
        columns = [column.name for column in cur.description]
        return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]


@pytest.fixture
def events(direct):
    times = [
        datetime(2031, 3, 1, 9, 0, tzinfo=UTC),
        datetime(2031, 3, 1, 12, 0, tzinfo=UTC),
        datetime(2031, 3, 1, 12, 0, tzinfo=UTC),
    ]
    # Event 3 has no resource_id, so NULLs are compared too (user_id is NULL in all).
    return [
        insert_event(
            direct,
            created_at,
            f"synthetic-{n}" if n != 3 else None,
            {"source": "test"} if n == 2 else None,
        )
        for n, created_at in enumerate(times, start=1)
    ]


@pytest.mark.parametrize(("limit", "offset"), [(10, 0), (1, 0), (1, 1), (2, 1), (5, 3)])
def test_records_order_keys_and_types_match_the_direct_query(direct, events, limit, offset):
    expected = direct_page(direct, limit, offset)

    result = get_audit_logs(limit=limit, offset=offset)

    assert result == expected
    assert [list(item) for item in result] == [FIELDS] * len(expected)
    assert [[type(v) for v in item.values()] for item in result] == [
        [type(v) for v in item.values()] for item in expected
    ]


def test_an_empty_table_gives_an_empty_list(direct):
    assert direct_page(direct, 50, 0) == []
    assert get_audit_logs() == []


def test_records_are_newest_first_with_id_as_tie_breaker_and_omit_details(events):
    result = get_audit_logs(limit=10, offset=0)

    first, second, third = events
    assert [item["id"] for item in result] == [third, second, first]
    assert all("details" not in item for item in result)
    assert result[0]["created_at"] == datetime(2031, 3, 1, 12, 0, tzinfo=UTC)


def test_the_connection_is_returned_to_the_pool_without_an_open_transaction(
    direct, disposable_database, events
):
    get_audit_logs(limit=10, offset=0)

    assert db_engine.get_engine().pool.checkedout() == 0
    open_transactions = direct.execute(
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE datname = %s AND pid <> pg_backend_pid() AND state LIKE 'idle in transaction%%'",
        (disposable_database.name,),
    ).fetchone()[0]
    assert open_transactions == 0


def test_the_read_never_commits(events):
    engine = db_engine.get_engine()
    seen = []

    def record(event_name):
        return lambda conn: seen.append(event_name)

    listeners = [("commit", record("commit")), ("rollback", record("rollback"))]
    for name, listener in listeners:
        event.listen(engine, name, listener)
    try:
        get_audit_logs(limit=10, offset=0)
    finally:
        for name, listener in listeners:
            event.remove(engine, name, listener)

    assert seen == ["rollback"]


def test_database_errors_propagate_and_the_connection_is_still_returned(monkeypatch, events):
    # Fault injection: a statement that fails in PostgreSQL (the table does not exist).
    monkeypatch.setattr(
        audit_service,
        "AUDIT_LOG_PAGE",
        text("SELECT * FROM missing_table LIMIT :limit OFFSET :offset"),
    )

    with pytest.raises(sa_exc.ProgrammingError) as raised:
        get_audit_logs(limit=10, offset=0)

    assert isinstance(raised.value.orig, psycopg.errors.UndefinedTable)
    assert db_engine.get_engine().pool.checkedout() == 0

    monkeypatch.undo()
    assert len(get_audit_logs(limit=10, offset=0)) == len(events)


def test_postgresql_refuses_writes_during_the_read(monkeypatch, direct, events):
    # Fault injection: if the read ever wrote, PostgreSQL itself would refuse it.
    monkeypatch.setattr(
        audit_service,
        "AUDIT_LOG_PAGE",
        text(
            "INSERT INTO audit_logs (action, resource_type, outcome) "
            "SELECT 'synthetic.write', 'patient', 'success' "
            "WHERE :limit > 0 AND :offset >= 0 RETURNING id"
        ),
    )

    with pytest.raises(sa_exc.InternalError) as raised:
        get_audit_logs(limit=10, offset=0)

    assert isinstance(raised.value.orig, psycopg.errors.ReadOnlySqlTransaction)
    assert direct.execute("SELECT count(*) FROM audit_logs").fetchone()[0] == len(events)


def test_the_read_only_setting_does_not_outlive_the_read(monkeypatch, direct):
    # A one-connection engine, so the write below provably reuses the read's connection.
    engine = db_engine.build_engine(
        {**os.environ, "DB_POOL_SIZE": "1", "DB_POOL_MAX_OVERFLOW": "0"}
    )
    monkeypatch.setattr(audit_service, "get_engine", lambda: engine)
    try:
        get_audit_logs()
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO audit_logs (action, resource_type, outcome) "
                "VALUES ('synthetic.write', 'patient', 'success')"
            )
    finally:
        engine.dispose()

    assert direct.execute("SELECT count(*) FROM audit_logs").fetchone()[0] == 1


def test_a_database_failure_is_a_plain_500_without_database_details(monkeypatch, events):
    sentinel = "Synthetic-Sensitive-Rowan-Ledger"
    monkeypatch.setattr(
        audit_service,
        "AUDIT_LOG_PAGE",
        text(
            "SELECT 'Synthetic-Sensitive-Rowan-Ledger' AS marker "
            "FROM missing_table LIMIT :limit OFFSET :offset"
        ),
    )
    previous = dict(main.app.dependency_overrides)
    main.app.dependency_overrides[get_current_user] = lambda: {
        "id": 1,
        "username": "synthetic.admin",
        "email": "admin@example.org",
        "role": "admin",
        "is_active": True,
    }
    try:
        response = TestClient(main.app, raise_server_exceptions=False).get("/audit-logs")
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous)

    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    assert sentinel not in response.text


@pytest.mark.parametrize(
    "limit,offset",
    [
        (0, 0),
        (101, 0),
        (10, -1),
    ],
)
def test_get_audit_logs_rejects_invalid_pagination(
    limit,
    offset,
):
    with patch("audit_service.get_engine") as mock_get_engine:
        with pytest.raises(
            ValueError, match=r"^(limit must be between 1 and 100|offset must be non-negative)$"
        ):
            get_audit_logs(limit=limit, offset=offset)

        mock_get_engine.assert_not_called()
