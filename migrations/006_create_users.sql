
CREATE TABLE IF NOT EXISTS users (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    username VARCHAR(50) NOT NULL UNIQUE,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,

    role VARCHAR(30) NOT NULL DEFAULT 'viewer',

    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT users_username_not_blank
        CHECK (LENGTH(TRIM(username)) > 0),

    CONSTRAINT users_email_not_blank
        CHECK (LENGTH(TRIM(email)) > 0),

    CONSTRAINT users_valid_role
        CHECK (role IN ('admin', 'clinician', 'scheduler', 'viewer'))
);

CREATE INDEX IF NOT EXISTS idx_users_role
ON users(role);
