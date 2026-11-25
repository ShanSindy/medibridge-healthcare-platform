"""
Provider reads on the shared SQLAlchemy engine, against this session's disposable database.

The expected results come from the pre-Phase 2 SQL (unchanged apart from whitespace), run
through psycopg on a separate, identity-checked connection. So these tests show that
moving get_all_providers, get_provider_by_id and search_providers onto the engine
changed neither the rows, their order, the totals, the dictionary keys nor the value
types, including for adversarial search values. Provider writes stay on
database.get_connection(); the mixed-path tests use both.
"""

import os
import threading
import time
from datetime import datetime

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import column, event, table
from sqlalchemy import exc as sa_exc

import db_engine
import main
import provider_service
from audit_service import get_audit_logs
from auth_dependencies import get_current_user
from fhir import provider_to_fhir, provider_to_practitioner_role
from models import ProviderCreate
from provider_service import (
    create_provider,
    delete_provider,
    get_all_providers,
    get_provider_by_id,
    search_providers,
    update_provider,
)
from tests.pg_helpers import verified_connection, verify_identity

FIELDS = ["id", "first_name", "last_name", "specialty", "email", "phone", "created_at"]

# --- the pre-Phase 2 queries, unchanged --------------------------------------------------

LEGACY_ALL = """
    SELECT id, first_name, last_name, specialty, email, phone, created_at
    FROM providers
    ORDER BY id
"""
LEGACY_BY_ID = """
    SELECT id, first_name, last_name, specialty, email, phone, created_at
    FROM providers
    WHERE id = %s
"""


def legacy_rows(conn, query, params=()) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(query, params)
        return [dict(zip(FIELDS, row, strict=True)) for row in cur.fetchall()]


def legacy_search(conn, family=None, given=None, count=10, offset=0, specialty=None):
    """search_providers as it was before Phase 2 (SQL and parameters unchanged)."""

    def prefix_pattern(value: str) -> str:
        escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"{escaped}%"

    conditions, params = [], []
    if family is not None:
        conditions.append("last_name ILIKE %s")
        params.append(prefix_pattern(family))
    if given is not None:
        conditions.append("first_name ILIKE %s")
        params.append(prefix_pattern(given))
    if specialty is not None:
        conditions.append("specialty ILIKE %s")
        params.append(prefix_pattern(specialty))
    where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

    rows = legacy_rows(
        conn,
        f"SELECT id, first_name, last_name, specialty, email, phone, created_at "  # noqa: S608
        f"FROM providers {where_clause} ORDER BY id LIMIT %s OFFSET %s",
        (*params, count, offset),
    )
    total = conn.execute(
        f"SELECT COUNT(*) FROM providers {where_clause}",  # noqa: S608
        tuple(params),
    ).fetchone()[0]
    return rows, total


def assert_same_records(actual: list[dict], expected: list[dict]) -> None:
    assert actual == expected
    assert [list(item) for item in actual] == [FIELDS] * len(expected)
    assert [[type(v) for v in item.values()] for item in actual] == [
        [type(v) for v in item.values()] for item in expected
    ]


# --- fixtures ----------------------------------------------------------------------------


@pytest.fixture
def direct(disposable_database):
    with verified_connection(disposable_database) as conn:
        yield conn


# Synthetic providers chosen for the search edge cases: case, prefixes that are also
# LIKE wildcards or the escape character, quotes, non-ASCII letters and NULLs.
SYNTHETIC_PROVIDERS = [
    ("Jane", "Smith", "Cardiology", "jane.smith@example.org", "555-0100"),
    ("jane", "SMYTHE", "cardiology", None, None),
    ("Ana", "Sm_th", "Card%iology", "ana@example.org", None),
    ("Bo", "O'Brien", "Neuro\\logy", None, "555-0101"),
    ("Zoë", "Ångström", "Dermatology", "zoe@example.org", None),
    ("Lee", "Smith", "Oncology", None, None),
    ("Ja_ne", "Smit%h", "Card_iology", None, None),
    ("Rui", "Ng", "Paediatrics", "rui@example.org", "555-0102"),
]


@pytest.fixture
def providers(direct):
    ids = []
    for first, last, specialty, email, phone in SYNTHETIC_PROVIDERS:
        ids.append(
            direct.execute(
                "INSERT INTO providers (first_name, last_name, specialty, email, phone) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (first, last, specialty, email, phone),
            ).fetchone()[0]
        )
    return ids


ADMIN = {
    "id": None,
    "username": "synthetic.provider.admin",
    "email": "provider.admin@example.org",
    "role": "admin",
    "is_active": True,
}


@pytest.fixture
def client():
    previous = dict(main.app.dependency_overrides)
    main.app.dependency_overrides[get_current_user] = lambda: ADMIN
    try:
        yield TestClient(main.app, raise_server_exceptions=False)
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous)


@pytest.fixture
def one_connection_engine(disposable_database, monkeypatch):
    """The process engine, replaced for one test by a one-connection pool with a 1 s wait."""
    engine = db_engine.build_engine(
        {
            **os.environ,
            "DB_POOL_SIZE": "1",
            "DB_POOL_MAX_OVERFLOW": "0",
            "DB_POOL_TIMEOUT_SECONDS": "1",
        }
    )
    with engine.connect() as conn:
        verify_identity(conn.connection.driver_connection, disposable_database)
    monkeypatch.setattr(db_engine, "_engine", engine)
    try:
        yield engine
    finally:
        engine.dispose()


# --- list and lookup ---------------------------------------------------------------------


def test_list_matches_the_legacy_query(direct, providers):
    # Updating the first rows stores new versions of them after the others, so a scan
    # without ORDER BY would no longer return them in id order.
    direct.execute("UPDATE providers SET phone = '555-0199' WHERE id = ANY(%s)", (providers[:3],))
    stored = [row[0] for row in direct.execute("SELECT id FROM providers").fetchall()]
    assert stored != sorted(providers)  # the precondition: storage order is not id order

    assert_same_records(get_all_providers(), legacy_rows(direct, LEGACY_ALL))
    assert [p["id"] for p in get_all_providers()] == sorted(providers)


def test_list_of_an_empty_table_is_empty(direct):
    assert legacy_rows(direct, LEGACY_ALL) == []
    assert get_all_providers() == []


def test_lookup_matches_the_legacy_query_for_every_provider(direct, providers):
    for provider_id in providers:
        [expected] = legacy_rows(direct, LEGACY_BY_ID, (provider_id,))
        assert_same_records([get_provider_by_id(provider_id)], [expected])
    assert isinstance(get_provider_by_id(providers[0])["created_at"], datetime)


@pytest.mark.parametrize("missing", [0, -1, 2_147_483_647])
def test_lookup_of_a_missing_provider_is_none(providers, missing):
    assert get_provider_by_id(missing) is None


# --- search ------------------------------------------------------------------------------

SEARCHES = {
    "no-filter": {},
    "family": {"family": "Sm"},
    "family-lowercase": {"family": "sm"},
    "family-uppercase": {"family": "SMY"},
    "given-case-insensitive": {"given": "JA"},
    "specialty": {"specialty": "card"},
    "family-and-given": {"family": "Sm", "given": "Ja"},
    "all-three": {"family": "Sm", "given": "Ja", "specialty": "Card"},
    "underscore-is-literal": {"family": "Sm_"},
    "percent-is-literal": {"family": "Smit%"},
    "percent-in-specialty": {"specialty": "Card%"},
    "underscore-in-given": {"given": "Ja_"},
    "backslash": {"specialty": "Neuro\\"},
    "lone-percent": {"given": "%"},
    "lone-underscore": {"given": "_"},
    "lone-backslash": {"family": "\\"},
    "quote": {"family": "O'"},
    "non-ascii": {"family": "Ång"},
    "non-ascii-other-case": {"family": "ång"},
    "empty-string-matches-all": {"family": ""},
    "sql-like": {"family": "'; DROP TABLE providers; --"},
    "sql-like-in-specialty": {"specialty": "x' OR '1'='1"},
    "no-match": {"family": "Zz"},
}


@pytest.mark.parametrize("filters", SEARCHES.values(), ids=SEARCHES.keys())
def test_search_matches_the_legacy_query(direct, providers, filters):
    expected_rows, expected_total = legacy_search(direct, **filters)

    rows, total = search_providers(**filters)

    assert_same_records(rows, expected_rows)
    assert total == expected_total
    assert direct.execute("SELECT count(*) FROM providers").fetchone()[0] == len(providers)


PAGES = [(10, 0), (1, 0), (1, 1), (3, 2), (100, 0), (2, 7), (5, 8), (5, 100)]


@pytest.mark.parametrize(("count", "offset"), PAGES)
@pytest.mark.parametrize(
    "filters",
    [{}, {"family": "Sm"}, {"family": "Sm", "given": "Ja", "specialty": "Card"}],
    ids=["all", "family", "all-three-filters"],
)
def test_search_pages_match_the_legacy_query(direct, providers, filters, count, offset):
    expected = legacy_search(direct, count=count, offset=offset, **filters)

    rows, total = search_providers(count=count, offset=offset, **filters)

    assert_same_records(rows, expected[0])
    assert total == expected[1]


@pytest.mark.parametrize(
    ("filters", "expected_last_names"),
    [
        # Fixed expectations, independent of the legacy comparison: wildcard characters
        # in a value match only themselves.
        ({"family": "Sm_"}, ["Sm_th"]),
        ({"family": "Smit%"}, ["Smit%h"]),
        ({"specialty": "Card_%"}, []),  # no specialty starts with the literal "Card_%"
        ({"specialty": "Card_"}, ["Smit%h"]),  # Card_iology
        ({"specialty": "Neuro\\"}, ["O'Brien"]),
        ({"family": "sm", "given": "JA"}, ["Smith", "SMYTHE", "Smit%h"]),
    ],
)
def test_search_matches_fixed_expectations(providers, filters, expected_last_names):
    rows, total = search_providers(**filters)

    assert [row["last_name"] for row in rows] == expected_last_names
    assert total == len(expected_last_names)


def test_an_empty_page_still_reports_the_full_total(providers):
    rows, total = search_providers(family="Sm", count=10, offset=100)

    assert rows == []
    assert total == 5  # Smith, SMYTHE, Sm_th, Smith, Smit%h


def test_search_of_an_empty_table(direct):
    assert search_providers() == ([], 0)
    assert legacy_search(direct) == ([], 0)


# --- transactions and connections --------------------------------------------------------

READS = {
    "list": lambda ids: get_all_providers(),
    "lookup": lambda ids: get_provider_by_id(ids[0]),
    "search": lambda ids: search_providers(family="Sm", count=2, offset=1),
}


@pytest.mark.parametrize("read", READS.values(), ids=READS.keys())
def test_reads_run_in_one_read_only_transaction_with_timeouts_and_never_commit(providers, read):
    engine = db_engine.get_engine()
    events, statements = [], []

    def on_begin(conn):
        events.append("begin")

    def on_commit(conn):
        events.append("commit")

    def on_rollback(conn):
        events.append("rollback")

    def before_execute(conn, cursor, statement, parameters, context, executemany):
        statements.append((statement, conn.connection.driver_connection.read_only))

    listeners = [
        ("begin", on_begin),
        ("commit", on_commit),
        ("rollback", on_rollback),
        ("before_cursor_execute", before_execute),
    ]
    for name, listener in listeners:
        event.listen(engine, name, listener)
    try:
        read(providers)
    finally:
        for name, listener in listeners:
            event.remove(engine, name, listener)

    assert events == ["begin", "rollback"]  # one transaction (page and count share it)
    # First the transaction-local timeouts, once; then only reads, all read-only.
    assert "'statement_timeout'" in statements[0][0]
    assert "'lock_timeout'" in statements[0][0]
    assert sum("set_config" in statement for statement, _ in statements) == 1
    assert all(statement.lstrip().startswith("SELECT") for statement, _ in statements)
    assert all(read_only for _, read_only in statements)
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize("read", READS.values(), ids=READS.keys())
def test_a_failing_read_propagates_and_returns_its_connection(monkeypatch, providers, read):
    # Fault injection: the same columns from a table that does not exist.
    monkeypatch.setattr(
        provider_service, "PROVIDERS", table("missing_providers", *(column(f) for f in FIELDS))
    )

    with pytest.raises(sa_exc.ProgrammingError) as raised:
        read(providers)

    assert isinstance(raised.value.orig, psycopg.errors.UndefinedTable)
    assert db_engine.get_engine().pool.checkedout() == 0


# --- endpoints ---------------------------------------------------------------------------

ENDPOINTS = ["/providers", "/fhir/Practitioner", "/fhir/PractitionerRole"]


@pytest.mark.parametrize("url", ENDPOINTS)
def test_an_exhausted_pool_gives_a_plain_500_within_the_wait_then_recovers(
    client, providers, one_connection_engine, url
):
    held = one_connection_engine.connect()  # the only connection
    try:
        started = time.monotonic()
        response = client.get(url)
        waited = time.monotonic() - started
    finally:
        held.close()

    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    assert 0.9 <= waited < 10
    assert one_connection_engine.pool.checkedout() == 0
    assert client.get(url).status_code == 200


def test_concurrent_requests_share_a_one_connection_pool_without_failing(
    client, providers, one_connection_engine
):
    # Two requests released together on a one-connection pool both succeed: with no
    # overflow allowed they can only take the one connection in turn. This does not show
    # that they overlapped, only that brief contention waits rather than fails.
    start = threading.Barrier(2, timeout=10)
    results = []

    def request():
        own_client = TestClient(main.app, raise_server_exceptions=False)
        start.wait()
        results.append(own_client.get("/providers").status_code)

    threads = [threading.Thread(target=request) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    assert not any(thread.is_alive() for thread in threads)
    assert results == [200, 200]
    assert one_connection_engine.pool.checkedout() == 0


def test_a_failing_read_is_a_plain_500_without_database_details(client, monkeypatch, providers):
    monkeypatch.setattr(
        provider_service,
        "PROVIDERS",
        table("synthetic_sensitive_missing_table", *(column(f) for f in FIELDS)),
    )

    for url in ENDPOINTS:
        response = client.get(url)
        assert (response.status_code, response.text) == (500, "Internal Server Error")


def test_rest_list_and_lookup_return_the_legacy_records(client, direct, providers):
    expected = legacy_rows(direct, LEGACY_ALL)

    listed = client.get("/providers")
    one = client.get(f"/providers/{providers[0]}")

    assert listed.status_code == 200
    assert [p["id"] for p in listed.json()] == [p["id"] for p in expected]
    assert one.status_code == 200
    assert one.json()["last_name"] == expected[0]["last_name"]
    assert datetime.fromisoformat(one.json()["created_at"]) == expected[0]["created_at"]
    missing = client.get("/providers/2147483647")
    assert (missing.status_code, missing.json()) == (404, {"detail": "Provider not found"})


def test_fhir_resources_are_built_from_the_legacy_records(client, direct, providers):
    [expected] = legacy_rows(direct, LEGACY_BY_ID, (providers[4],))

    practitioner = client.get(f"/fhir/Practitioner/{providers[4]}")
    role = client.get(f"/fhir/PractitionerRole/{providers[4]}")

    assert practitioner.json() == provider_to_fhir(expected)
    assert role.json() == provider_to_practitioner_role(expected)


@pytest.mark.parametrize(
    ("url", "filters", "count", "offset"),
    [
        ("/fhir/Practitioner?family=sm&_count=2&_offset=1", {"family": "sm"}, 2, 1),
        ("/fhir/Practitioner?given=ja", {"given": "ja"}, 10, 0),
        ("/fhir/PractitionerRole?specialty=card&_count=1", {"specialty": "card"}, 1, 0),
    ],
)
def test_fhir_search_bundles_match_the_legacy_search(
    client, direct, providers, url, filters, count, offset
):
    expected_rows, expected_total = legacy_search(direct, count=count, offset=offset, **filters)

    bundle = client.get(url).json()

    assert bundle["total"] == expected_total
    assert [entry["resource"]["id"] for entry in bundle["entry"]] == [
        str(row["id"]) for row in expected_rows
    ]


# --- legacy writes and pooled reads together ----------------------------------------------


def synthetic(last_name, email):
    return ProviderCreate(
        first_name="Mixed",
        last_name=last_name,
        specialty="Synthetic Medicine",
        email=email,
        phone=None,
    )


def test_a_legacy_write_is_visible_to_the_next_pooled_read():
    created = create_provider(synthetic("Pathway", "mixed.pathway@example.org"))

    assert get_provider_by_id(created["id"]) == created
    assert search_providers(family="Pathway") == ([created], 1)


def test_pooled_reads_and_legacy_writes_alternate_without_interference(direct):
    created = create_provider(synthetic("Before", "mixed.before@example.org"))
    assert get_all_providers() == [created]

    updated = update_provider(created["id"], synthetic("After", "mixed.after@example.org"))
    assert get_provider_by_id(created["id"]) == updated
    assert get_audit_logs() == []  # the other engine consumer is unaffected

    assert delete_provider(created["id"]) is True
    assert get_provider_by_id(created["id"]) is None
    assert db_engine.get_engine().pool.checkedout() == 0
    open_transactions = direct.execute(
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE datname = current_database() AND pid <> pg_backend_pid() "
        "AND state LIKE 'idle in transaction%'"
    ).fetchone()[0]
    assert open_transactions == 0
