# ADR 0003: Alembic baseline (fresh installations only)

Status: proposed. Date: 2026-11-26. Builds on ADR 0002 (schema metadata and drift check).

## Context

Schema changes are plain SQL migrations applied by `migrate.py`, recorded in
`schema_migrations`. Future schema changes should use Alembic, without losing the
history, the ledger or the data of existing databases, and without a period in which
two tools might change the same database.

## Database states

`schema_inspection.inspect_migration_state()` classifies the public schema of a
connected, identity-checked database, read-only:

| State | Meaning | Behaviour |
|---|---|---|
| `fresh` | no tables, views or foreign tables in `public` | Alembic may build the baseline |
| `alembic-baseline` | the baseline schema and `alembic_version` = `0001_baseline` | upgrade is a no-op |
| `eligible-for-future-adoption-not-adopted` | built by `migrate.py`: exact ledger (the eight files, nothing else, expected structure) and no drift | nothing; a future, separately authorized step may consider it |
| `invalid-historical-ledger` | ledger rows or structure differ | refused |
| `schema-drift` | ledger or version valid, schema differs (ADR 0002 comparison) | refused |
| `unsupported` | both ledgers, unexpected Alembic versions, application objects without a ledger | refused |

A database with equivalent tables but an invalid ledger, or an incomplete one, never
qualifies. Inspection does not create, repair, stamp or adopt anything.

## Decision

- **Dependency.** `alembic==1.20.1` (needs SQLAlchemy 2.x; brings Mako and MarkupSafe)
  is pinned in `requirements-migrations.txt`, which includes `requirements.txt`. The
  application runtime (`requirements.txt`) does not install it. `requirements-dev.txt`
  includes the migrations file, so CI installs and audits Alembic in both the backend
  and the dependency-audit jobs. `pip check` and `pip-audit` pass.
- **Shared migration lock.** `migrate.py` and the Alembic environment take the same
  PostgreSQL advisory lock (`schema_inspection.MIGRATION_LOCK_KEY`): `migrate.py` for its
  whole session (`pg_try_advisory_lock`, released when its connection closes), Alembic for
  its transaction (`pg_try_advisory_xact_lock`). Either fails immediately while the other
  holds it. `migrate.py` also refuses a database that has `alembic_version`, before
  creating or changing anything; on any other database it behaves as before.
- **Verification before commit.** After the migrations run, still inside the
  transaction, the environment compares the result with `db_schema.metadata` using the
  ADR 0002 comparison (`verify_against_metadata`: the expected schema is created under a
  savepoint that is always rolled back). Any difference raises and rolls everything
  back. This covers the fresh baseline and re-runs alike: a version row is never taken
  as proof of the schema. The check needs `CREATE` on the database for the scratch
  schema.
- **Baseline revision `0001_baseline`** runs the historical SQL files 000-007 themselves,
  in order, after checking each against a pinned SHA-256 of its text (byte order mark
  removed as `migrate.py` removes it, LF line endings, so Windows and Linux checkouts
  agree). So a fresh Alembic install builds exactly what `migrate.py` builds, and an edit
  to a historical migration stops the baseline before any change. It does not write
  `schema_migrations`; Alembic records its version in `alembic_version`. Downgrade raises.
  Alternatives rejected: autogenerate (does not reproduce the exclusion constraint and
  identity details reliably) and building from `db_schema` (the revision would change
  whenever the metadata does; revisions must be immutable).
- **Environment (`alembic/env.py`)** runs the upgrade in one transaction (transactional
  DDL): a failure rolls back every change, including the version row. Inside it, before
  any change, it
  - takes `pg_try_advisory_xact_lock` on a fixed key and fails immediately if another
    migration holds it (no waiting; released at commit or rollback, also on connection
    loss);
  - checks `current_database()` against the expected name;
  - forces `search_path` to `public` for the rest of the transaction;
  - allows only an empty database (no schema but `public`, no extension but plpgsql,
    no object of any compared kind) or a re-run at the baseline (the relation names, the
    version row and no extra schemas or extensions; full equivalence is checked by
    `inspect_migration_state`); anything else is refused with `MigrationRefused`.
  - A caller connection already in a transaction is refused, so Alembic's own commit
    always applies. On the command line the operator must name the target,
    `alembic -x expected_database=<DB_NAME> upgrade head`, and it must match `DB_NAME`;
    the `current_database()` check then compares against that explicit name.
  Connections come from the `DB_*` settings through the shared engine factory (statement
  and lock timeouts apply), or from the caller in tests. `alembic.ini` holds no URL or
  credentials; refusal messages name states, not settings. Offline mode is refused.
- **Reusable inspection (`schema_inspection.py`)** holds the ADR 0002 catalog snapshot
  and comparison, moved from the test module without changing their semantics, plus
  `materialize_expected()` (writes only into a new scratch schema, for a caller that
  rolls back) and the state classifier. It opens no connection and creates no engine;
  the application never imports it (tested).
- `migrate.py` and the historical migrations are unchanged and remain the way every
  existing environment is built.

## Adoption of existing databases (designed, not implemented)

Adoption would be a separate command, run only with explicit maintainer authorization
for a named database, in one transaction:

1. Verify the database identity (name and the operator's expected OID).
2. Take the migration advisory lock.
3. Require state `eligible-for-future-adoption-not-adopted`: the exact eight-row ledger
   with its expected structure, no `alembic_version`, and zero drift against the
   metadata (expected schema materialized in a rolled-back scratch schema of a
   disposable database, never in the target).
4. Create `alembic_version` with `0001_baseline`, keeping `schema_migrations` unchanged.
5. Re-inspect inside the transaction; commit only if the state is now
   `alembic-baseline` with the ledger intact.

Nothing is inferred: equivalent tables with a different ledger, extra objects or drift
mean no adoption. Ledger rows are never deleted, rewritten or invented. Until adoption
exists, Alembic-built and `migrate.py`-built databases stay separate: without the guard,
`migrate.py` on an Alembic-built database would succeed for 000-004 (`IF NOT EXISTS`) and
commit their ledger rows before 005 failed, leaving a database both tools refuse. The
guard in `migrate.py` now refuses that before any change.

## What is and is not guaranteed

- Guaranteed: one migration tool run at a time per database, among `migrate.py` and
  Alembic (both take the lock); no partial baseline; no commit unless the result matches
  the metadata; no change to a refused database; no change to historical files without
  detection; `migrate.py` never runs on an Alembic-managed database.
- Not guaranteed: protection against any other DDL session that does not take the lock (its statements still wait on table locks, bounded by `lock_timeout`);
  a connection lost after `COMMIT` is sent but before the reply leaves the outcome to be
  established by re-inspection, which is safe to retry because a completed baseline makes
  the next upgrade a no-op.
- Extension creation needs `CREATE` on the database and a trusted extension
  (`btree_gist` is trusted); a role without that fails in the transaction and changes
  nothing.

## Deployment prerequisites

- Install `requirements-migrations.txt` where migrations run (not needed by the
  application itself).
- Run from the repository root, with the `DB_*` settings of the target database:
  `alembic -x expected_database=<DB_NAME> upgrade head`. Only a fresh, empty database is
  accepted; existing databases keep using `migrate.py` until adoption is authorized.
- The migration role needs `CREATE` on the database (scratch schema for verification,
  trusted extension `btree_gist`) and ownership of the objects it creates.
- The checkout must contain the historical migrations 000-007 unchanged (their pinned
  hashes are checked before any change).

Creating new revisions (`alembic revision`) is not set up yet: there is no
`alembic/script.py.mako` template, so the next schema change adds it.

## CI

The existing backend job runs both paths: the session database is built by `migrate.py`,
and the Alembic tests build fresh disposable databases with Alembic and compare them
with it, as the same non-superuser `LOGIN CREATEDB` role. No workflow change is needed.
