-- Migration 000: Create the foundational patients table.
-- Must run before 001_create_patient_identifiers.sql.

CREATE TABLE IF NOT EXISTS patients (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100) NOT NULL,
    date_of_birth DATE NOT NULL,
    gender VARCHAR(20) NOT NULL
);
