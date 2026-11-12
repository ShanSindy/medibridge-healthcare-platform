
CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    patient_id INTEGER NOT NULL
        REFERENCES patients(id) ON DELETE RESTRICT,

    provider_id INTEGER NOT NULL
        REFERENCES providers(id) ON DELETE RESTRICT,

    starts_at TIMESTAMPTZ NOT NULL,
    ends_at TIMESTAMPTZ NOT NULL,

    status VARCHAR(20) NOT NULL DEFAULT 'scheduled',

    reason VARCHAR(500),

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT appointments_valid_time
        CHECK (ends_at > starts_at),

    CONSTRAINT appointments_valid_status
        CHECK (
            status IN (
                'scheduled',
                'completed',
                'cancelled'
            )
        )
);

CREATE INDEX IF NOT EXISTS idx_appointments_patient_id
    ON appointments(patient_id);

CREATE INDEX IF NOT EXISTS idx_appointments_provider_id
    ON appointments(provider_id);

CREATE INDEX IF NOT EXISTS idx_appointments_starts_at
    ON appointments(starts_at);
