
-- Migration 001: Create patient identifiers table
-- Requires the patients table to already exist.

CREATE TABLE IF NOT EXISTS patient_identifiers (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id INTEGER NOT NULL,
    system VARCHAR(255) NOT NULL,
    value VARCHAR(100) NOT NULL,

    CONSTRAINT fk_patient_identifiers_patient
        FOREIGN KEY (patient_id)
        REFERENCES patients(id)
        ON DELETE CASCADE,

    CONSTRAINT unique_patient_identifier
        UNIQUE (system, value)
);
