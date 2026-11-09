
CREATE TABLE IF NOT EXISTS hl7_messages (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sending_application TEXT NOT NULL,
    sending_facility TEXT NOT NULL,
    message_control_id TEXT NOT NULL,
    message_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'processed',
    received_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_hl7_message_source_control
        UNIQUE (
            sending_application,
            sending_facility,
            message_control_id
        ),

    CONSTRAINT chk_hl7_message_status
        CHECK (status IN ('processed', 'failed'))
);
