# ADR 0002: Schema metadata and drift check

Status: proposed. Date: 2026-11-26.

## Context

The schema is defined by plain SQL migrations (`migrations/000`-`007`), applied in order
by `migrate.py` and recorded in `schema_migrations`. Nothing described the resulting
schema as a whole, and `tests/test_pg_schema.py` checks selected catalog facts by hand.
Adopting Alembic later needs a trusted description of today's schema to start from.

## Decision

- `db_schema.py` describes the schema as SQLAlchemy Core metadata: every table, column
  type, nullability, server default, identity column, primary key, foreign key (with its
  delete action), unique, check and exclusion constraint, index, and the required
  `btree_gist` extension. Every constraint carries the name the migrations gave it, or
  the name PostgreSQL generated where they gave none (for example
  `providers_email_key`), so names are deterministic.
- The metadata is descriptive only. The migrations remain the only thing that changes a
  database; nothing at runtime imports `db_schema` or creates tables from it (a test
  enforces this). The services keep their own untyped `table()` definitions, because
  typed columns would add bind casts and change existing behaviour.
- `tests/test_schema_drift.py` compares the metadata with the migrated schema:
  - The migrated schema is `public` in the test session's disposable database. The
    expected schema is created from the metadata in a scratch schema inside one bounded
    transaction that is always rolled back; the check refuses to start if any schema
    other than `public` exists.
  - Both are read with the same catalog queries and PostgreSQL's own deparsers, with
    `search_path` set to the schema under test followed by `public`. References within
    the schema print unqualified, the `btree_gist` objects in `public` print the same in
    both, and any other cross-schema reference prints qualified and shows up as a
    difference. Foreign-key targets, indexes and identity sequences are also checked to
    lie in the schema under test. The only rewrite is the schema qualifier that
    `pg_get_indexdef` always puts on the indexed table.
  - Defaults are compared as stored: `NOW()` and `CURRENT_TIMESTAMP` are different
    expressions to PostgreSQL, and the metadata writes each as its migration did.
  - Compared: the complete inventory of tables and other relations (views, sequences,
    indexes), functions, types, collations and operators in the schema (extension
    members excluded); column order (as ordinal positions, so a dropped and re-added
    column is not drift), name, type, collation, nullability, default, identity mode and
    generated status; identity sequence type, start, increment, bounds, cache and cycle;
    every constraint's type, columns, definition, deferrability, validation, NO INHERIT
    flag and foreign-key target, actions and match type; every index's definition
    (expressions, predicate), uniqueness, validity and operator classes; table options,
    persistence, row-level security, replica identity, access method and inheritance
    parents; user triggers by definition and enabled state, and internal (foreign-key)
    triggers by count and enabled state per table; row-level security policies; rules;
    extended statistics; and, database-wide, the installed extensions with their
    schemas, which must be exactly plpgsql and the required btree_gist.
  - Not compared, deliberately: the ledger table `schema_migrations` (created by
    `migrate.py`, not a migration) and its index; OIDs, owners, privileges and
    tablespaces; planner statistics, storage and comments; extension versions; current
    sequence values; internal trigger names (generated from OIDs). Disabling internal
    triggers needs superuser, so that case is compared but not exercised by a test.
  - Negative tests change copies of the metadata and, inside rolled-back transactions,
    the migrated schema, and require each change to appear in the expected section of
    the comparison. They include an unexpected extension, trigger, policy, rule,
    statistics object, view, function, sequence and table, and a foreign key to a table
    of the same name in another schema.

## Consequences

- A change to a migration without the matching metadata change, or the reverse, fails
  the test suite.
- The check needs `CREATE` on the test database (to create the scratch schema), which
  the database owner has; it uses no superuser statements. It runs in CI as the
  non-superuser test role.
- Each table is described twice for now (typed metadata, untyped runtime queries).
  Consolidating them is a separate, later decision.

## Preparation for Alembic (Milestone 6)

Zero drift is necessary but not sufficient for adopting Alembic. Adoption also has to
keep the existing `schema_migrations` ledger exactly as it is, decide how the two
ledgers coexist, and stamp an existing database only through a guarded, explicitly
authorized step after verifying its identity, its ledger and its drift. Nothing in this
decision stamps or migrates a database.
