ALTER TABLE postcall_jobs
    ADD COLUMN IF NOT EXISTS locked_until timestamptz,
    ADD COLUMN IF NOT EXISTS attempt_count integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS max_attempts integer NOT NULL DEFAULT 3,
    ADD COLUMN IF NOT EXISTS next_run_at timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS last_heartbeat_at timestamptz;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'postcall_jobs_attempts_valid'
    ) THEN
        ALTER TABLE postcall_jobs
            ADD CONSTRAINT postcall_jobs_attempts_valid
            CHECK (
                attempt_count >= 0
                AND max_attempts > 0
                AND attempt_count <= max_attempts
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'postcall_jobs_lock_window_valid'
    ) THEN
        ALTER TABLE postcall_jobs
            ADD CONSTRAINT postcall_jobs_lock_window_valid
            CHECK (
                locked_until IS NULL
                OR locked_at IS NULL
                OR locked_until >= locked_at
            );
    END IF;
END;
$$;

CREATE INDEX IF NOT EXISTS idx_postcall_jobs_queue_ready
    ON postcall_jobs (status, priority DESC, next_run_at, created_at);

CREATE INDEX IF NOT EXISTS idx_postcall_jobs_lock_expiry
    ON postcall_jobs (locked_until)
    WHERE locked_until IS NOT NULL;

CREATE TABLE IF NOT EXISTS postcall_callback_deliveries (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    postcall_job_id uuid NOT NULL REFERENCES postcall_jobs(id) ON DELETE CASCADE,
    delivery_type text NOT NULL DEFAULT 'analysis_completed',
    callback_url text NOT NULL,
    status text NOT NULL DEFAULT 'pending',
    attempt_count integer NOT NULL DEFAULT 0,
    max_attempts integer NOT NULL DEFAULT 5,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    last_attempt_at timestamptz,
    succeeded_at timestamptz,
    request_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    response_status integer,
    response_body text,
    error_code text,
    error_message text,
    locked_by text,
    locked_at timestamptz,
    locked_until timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT postcall_callback_deliveries_job_type_key UNIQUE (
        postcall_job_id,
        delivery_type
    ),
    CONSTRAINT postcall_callback_deliveries_type_valid CHECK (
        delivery_type IN ('analysis_completed', 'analysis_failed')
    ),
    CONSTRAINT postcall_callback_deliveries_url_not_blank CHECK (btrim(callback_url) <> ''),
    CONSTRAINT postcall_callback_deliveries_status_valid CHECK (
        status IN ('pending', 'running', 'succeeded', 'failed', 'abandoned')
    ),
    CONSTRAINT postcall_callback_deliveries_attempts_valid CHECK (
        attempt_count >= 0
        AND max_attempts > 0
        AND attempt_count <= max_attempts
    ),
    CONSTRAINT postcall_callback_deliveries_response_status_valid CHECK (
        response_status IS NULL
        OR (response_status >= 100 AND response_status <= 599)
    ),
    CONSTRAINT postcall_callback_deliveries_lock_window_valid CHECK (
        locked_until IS NULL
        OR locked_at IS NULL
        OR locked_until >= locked_at
    ),
    CONSTRAINT postcall_callback_deliveries_json_objects CHECK (
        jsonb_typeof(request_payload) = 'object'
    )
);

CREATE INDEX IF NOT EXISTS idx_postcall_callback_deliveries_job_created_at
    ON postcall_callback_deliveries (postcall_job_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_postcall_callback_deliveries_ready
    ON postcall_callback_deliveries (status, next_attempt_at, created_at);

CREATE INDEX IF NOT EXISTS idx_postcall_callback_deliveries_lock_expiry
    ON postcall_callback_deliveries (locked_until)
    WHERE locked_until IS NOT NULL;

DROP TRIGGER IF EXISTS trg_postcall_callback_deliveries_updated_at
    ON postcall_callback_deliveries;

CREATE TRIGGER trg_postcall_callback_deliveries_updated_at
BEFORE UPDATE ON postcall_callback_deliveries
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();
