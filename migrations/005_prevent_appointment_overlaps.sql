
-- Enable PostgreSQL GiST equality support for integers.
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- Prevent overlapping scheduled appointments
-- for the same provider.
ALTER TABLE appointments
ADD CONSTRAINT appointments_no_provider_overlap
EXCLUDE USING gist (
    provider_id WITH =,
    tstzrange(starts_at, ends_at, '[)') WITH &&
)
WHERE (status = 'scheduled');
