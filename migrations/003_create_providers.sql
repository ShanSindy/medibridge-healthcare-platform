
CREATE TABLE IF NOT EXISTS providers (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100) NOT NULL,
    specialty VARCHAR(150) NOT NULL,
    email VARCHAR(255) UNIQUE,
    phone VARCHAR(30),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT providers_first_name_not_blank
        CHECK (LENGTH(TRIM(first_name)) > 0),

    CONSTRAINT providers_last_name_not_blank
        CHECK (LENGTH(TRIM(last_name)) > 0),

    CONSTRAINT providers_specialty_not_blank
        CHECK (LENGTH(TRIM(specialty)) > 0)
);
