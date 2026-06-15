ALTER TABLE postcall_jobs
    ADD COLUMN IF NOT EXISTS call_id text;

ALTER TABLE postcall_jobs
    DROP CONSTRAINT IF EXISTS postcall_jobs_call_id_not_blank;

ALTER TABLE postcall_jobs
    ADD CONSTRAINT postcall_jobs_call_id_not_blank
    CHECK (call_id IS NULL OR btrim(call_id) <> '');

CREATE INDEX IF NOT EXISTS idx_postcall_jobs_call_id
    ON postcall_jobs (call_id)
    WHERE call_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS postcall_realtime_streams (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    call_id text NOT NULL,
    voice_id text,
    agent_id text,
    usrdn text,
    vendor_specific_param text,
    state text NOT NULL DEFAULT 'receiving',
    received_duration_sec numeric(12, 3) NOT NULL DEFAULT 0,
    analyzed_duration_sec numeric(12, 3) NOT NULL DEFAULT 0,
    linked_postcall_job_id uuid REFERENCES postcall_jobs(id) ON DELETE SET NULL,
    final_audio_path text,
    ended_at timestamptz,
    finalized_at timestamptz,
    error_code text,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT postcall_realtime_streams_call_id_key UNIQUE (call_id),
    CONSTRAINT postcall_realtime_streams_call_id_not_blank CHECK (btrim(call_id) <> ''),
    CONSTRAINT postcall_realtime_streams_state_valid CHECK (
        state IN ('receiving', 'analyzing', 'alerting', 'ended', 'finalized', 'failed')
    ),
    CONSTRAINT postcall_realtime_streams_duration_valid CHECK (
        received_duration_sec >= 0
        AND analyzed_duration_sec >= 0
        AND analyzed_duration_sec <= received_duration_sec
    )
);

CREATE INDEX IF NOT EXISTS idx_postcall_realtime_streams_state_updated
    ON postcall_realtime_streams (state, updated_at);

CREATE INDEX IF NOT EXISTS idx_postcall_realtime_streams_linked_job
    ON postcall_realtime_streams (linked_postcall_job_id)
    WHERE linked_postcall_job_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS postcall_realtime_chunks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    stream_id uuid NOT NULL REFERENCES postcall_realtime_streams(id) ON DELETE CASCADE,
    seq integer NOT NULL,
    file_path text NOT NULL,
    sha256 text NOT NULL,
    size_bytes bigint NOT NULL,
    duration_sec numeric(12, 3),
    sample_rate integer,
    channels integer,
    received_at timestamptz NOT NULL DEFAULT now(),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT postcall_realtime_chunks_stream_seq_key UNIQUE (stream_id, seq),
    CONSTRAINT postcall_realtime_chunks_seq_non_negative CHECK (seq >= 0),
    CONSTRAINT postcall_realtime_chunks_file_path_not_blank CHECK (btrim(file_path) <> ''),
    CONSTRAINT postcall_realtime_chunks_size_positive CHECK (size_bytes > 0),
    CONSTRAINT postcall_realtime_chunks_audio_positive CHECK (
        (duration_sec IS NULL OR duration_sec >= 0)
        AND (sample_rate IS NULL OR sample_rate > 0)
        AND (channels IS NULL OR channels > 0)
    ),
    CONSTRAINT postcall_realtime_chunks_metadata_object CHECK (jsonb_typeof(metadata) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_postcall_realtime_chunks_stream_seq
    ON postcall_realtime_chunks (stream_id, seq);

CREATE TABLE IF NOT EXISTS postcall_realtime_windows (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    stream_id uuid NOT NULL REFERENCES postcall_realtime_streams(id) ON DELETE CASCADE,
    window_index integer NOT NULL,
    start_sec numeric(12, 3) NOT NULL,
    end_sec numeric(12, 3) NOT NULL,
    state text NOT NULL DEFAULT 'completed',
    timeline_segments jsonb NOT NULL DEFAULT '[]'::jsonb,
    attention_level smallint,
    attention_level_name text,
    emotion_types_zh text[] NOT NULL DEFAULT ARRAY[]::text[],
    review_segments jsonb NOT NULL DEFAULT '[]'::jsonb,
    matched_rule_codes text[] NOT NULL DEFAULT ARRAY[]::text[],
    error_code text,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    CONSTRAINT postcall_realtime_windows_stream_index_key UNIQUE (stream_id, window_index),
    CONSTRAINT postcall_realtime_windows_index_non_negative CHECK (window_index >= 0),
    CONSTRAINT postcall_realtime_windows_time_valid CHECK (
        start_sec >= 0
        AND end_sec >= start_sec
    ),
    CONSTRAINT postcall_realtime_windows_state_valid CHECK (
        state IN ('completed', 'failed')
    ),
    CONSTRAINT postcall_realtime_windows_attention_valid CHECK (
        attention_level IS NULL OR attention_level IN (1, 2, 3)
    ),
    CONSTRAINT postcall_realtime_windows_json_valid CHECK (
        jsonb_typeof(timeline_segments) = 'array'
        AND jsonb_typeof(review_segments) = 'array'
    )
);

CREATE INDEX IF NOT EXISTS idx_postcall_realtime_windows_stream_time
    ON postcall_realtime_windows (stream_id, start_sec, end_sec);

CREATE TABLE IF NOT EXISTS postcall_realtime_alert_notifications (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    stream_id uuid NOT NULL REFERENCES postcall_realtime_streams(id) ON DELETE CASCADE,
    postcall_job_id uuid REFERENCES postcall_jobs(id) ON DELETE SET NULL,
    jjdh text,
    call_id text NOT NULL,
    level smallint NOT NULL,
    level_name text NOT NULL,
    emotion_types_zh text[] NOT NULL DEFAULT ARRAY[]::text[],
    payload jsonb NOT NULL,
    state text NOT NULL DEFAULT 'skipped',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT postcall_realtime_alert_call_id_not_blank CHECK (btrim(call_id) <> ''),
    CONSTRAINT postcall_realtime_alert_level_valid CHECK (level IN (1, 2)),
    CONSTRAINT postcall_realtime_alert_state_valid CHECK (
        state IN ('pending', 'sent', 'failed', 'skipped')
    ),
    CONSTRAINT postcall_realtime_alert_payload_object CHECK (jsonb_typeof(payload) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_postcall_realtime_alert_stream_created
    ON postcall_realtime_alert_notifications (stream_id, created_at DESC);
