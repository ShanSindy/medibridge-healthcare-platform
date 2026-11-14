-- Migration 007: Create the audit logs table.
-- Requires migration 006_create_users.sql.

CREATE TABLE IF NOT EXISTS audit_logs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action VARCHAR(100) NOT NULL,
    resource_type VARCHAR(100) NOT NULL,
    resource_id VARCHAR(100),
    outcome VARCHAR(20) NOT NULL
        CHECK (outcome IN ('success', 'failure')),
    details JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
