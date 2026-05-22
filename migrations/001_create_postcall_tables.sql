CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS postcall_jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id text NOT NULL DEFAULT ('job_' || replace(gen_random_uuid()::text, '-', '')),
    jjdh text NOT NULL,
    audio_url text NOT NULL,
    bjsj timestamptz NOT NULL,
    jcjxtjsdwmc text NOT NULL,
    jjdwmc text NOT NULL,
    gxdwmc text NOT NULL,
    bjdh text NOT NULL,
    bjrmc text NOT NULL,
    bjrxbdm smallint NOT NULL,
    lxdh text NOT NULL,
    jqdz text NOT NULL,
    bjnr text NOT NULL,
    jqlbdm text NOT NULL,
    jqlxdm text NOT NULL,
    jqxldm text,
    jqzldm text,
    jqdj text NOT NULL,
    callback_url text,
    asr_result jsonb NOT NULL DEFAULT '[]'::jsonb,
    raw_payload jsonb NOT NULL,
    client_id text NOT NULL,
    source_system text NOT NULL,
    status text NOT NULL DEFAULT 'queued',
    priority smallint NOT NULL DEFAULT 0,
    duplicate_count integer NOT NULL DEFAULT 0,
    locked_by text,
    locked_at timestamptz,
    started_at timestamptz,
    completed_at timestamptz,
    failed_at timestamptz,
    error_code text,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT postcall_jobs_job_id_key UNIQUE (job_id),
    CONSTRAINT postcall_jobs_jjdh_key UNIQUE (jjdh),
    CONSTRAINT postcall_jobs_jjdh_not_blank CHECK (btrim(jjdh) <> ''),
    CONSTRAINT postcall_jobs_audio_url_not_blank CHECK (btrim(audio_url) <> ''),
    CONSTRAINT postcall_jobs_required_text_not_blank CHECK (
        btrim(jcjxtjsdwmc) <> ''
        AND btrim(jjdwmc) <> ''
        AND btrim(gxdwmc) <> ''
        AND btrim(bjdh) <> ''
        AND btrim(bjrmc) <> ''
        AND btrim(lxdh) <> ''
        AND btrim(jqdz) <> ''
        AND btrim(bjnr) <> ''
        AND btrim(jqlbdm) <> ''
        AND btrim(jqlxdm) <> ''
        AND btrim(jqdj) <> ''
        AND btrim(client_id) <> ''
        AND btrim(source_system) <> ''
    ),
    CONSTRAINT postcall_jobs_optional_text_not_blank CHECK (
        (jqxldm IS NULL OR btrim(jqxldm) <> '')
        AND (jqzldm IS NULL OR btrim(jqzldm) <> '')
        AND (callback_url IS NULL OR btrim(callback_url) <> '')
    ),
    CONSTRAINT postcall_jobs_bjrxbdm_valid CHECK (bjrxbdm IN (0, 1, 2)),
    CONSTRAINT postcall_jobs_status_valid CHECK (
        status IN ('queued', 'downloading', 'analyzing', 'completed', 'failed', 'cancelled')
    ),
    CONSTRAINT postcall_jobs_duplicate_count_non_negative CHECK (duplicate_count >= 0),
    CONSTRAINT postcall_jobs_asr_result_array CHECK (jsonb_typeof(asr_result) = 'array'),
    CONSTRAINT postcall_jobs_raw_payload_object CHECK (jsonb_typeof(raw_payload) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_postcall_jobs_status_created_at
    ON postcall_jobs (status, created_at);

CREATE INDEX IF NOT EXISTS idx_postcall_jobs_bjsj
    ON postcall_jobs (bjsj);

CREATE INDEX IF NOT EXISTS idx_postcall_jobs_client_created_at
    ON postcall_jobs (client_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_postcall_jobs_source_created_at
    ON postcall_jobs (source_system, created_at DESC);

CREATE TABLE IF NOT EXISTS postcall_job_duplicate_submissions (
    id bigserial PRIMARY KEY,
    postcall_job_id uuid NOT NULL REFERENCES postcall_jobs(id) ON DELETE CASCADE,
    jjdh text NOT NULL,
    submitted_audio_url text NOT NULL,
    original_audio_url text NOT NULL,
    raw_payload jsonb NOT NULL,
    client_id text NOT NULL,
    source_system text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT postcall_duplicate_jjdh_not_blank CHECK (btrim(jjdh) <> ''),
    CONSTRAINT postcall_duplicate_urls_not_blank CHECK (
        btrim(submitted_audio_url) <> ''
        AND btrim(original_audio_url) <> ''
    ),
    CONSTRAINT postcall_duplicate_client_not_blank CHECK (
        btrim(client_id) <> ''
        AND btrim(source_system) <> ''
    ),
    CONSTRAINT postcall_duplicate_raw_payload_object CHECK (jsonb_typeof(raw_payload) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_postcall_duplicate_job_created_at
    ON postcall_job_duplicate_submissions (postcall_job_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_postcall_duplicate_jjdh_created_at
    ON postcall_job_duplicate_submissions (jjdh, created_at DESC);

CREATE TABLE IF NOT EXISTS postcall_audio_assets (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    postcall_job_id uuid NOT NULL REFERENCES postcall_jobs(id) ON DELETE CASCADE,
    asset_type text NOT NULL,
    uri text NOT NULL,
    content_type text,
    sha256 text,
    sample_rate integer,
    channels integer,
    duration_sec numeric(10, 3),
    size_bytes bigint,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT postcall_audio_assets_type_valid CHECK (
        asset_type IN ('source', 'normalized', 'vad_segment', 'evidence_clip')
    ),
    CONSTRAINT postcall_audio_assets_uri_not_blank CHECK (btrim(uri) <> ''),
    CONSTRAINT postcall_audio_assets_positive_audio CHECK (
        (sample_rate IS NULL OR sample_rate > 0)
        AND (channels IS NULL OR channels > 0)
        AND (duration_sec IS NULL OR duration_sec >= 0)
        AND (size_bytes IS NULL OR size_bytes >= 0)
    ),
    CONSTRAINT postcall_audio_assets_metadata_object CHECK (jsonb_typeof(metadata) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_postcall_audio_assets_job_type
    ON postcall_audio_assets (postcall_job_id, asset_type);

CREATE TABLE IF NOT EXISTS postcall_model_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    postcall_job_id uuid NOT NULL REFERENCES postcall_jobs(id) ON DELETE CASCADE,
    model_name text NOT NULL,
    model_version text,
    model_role text NOT NULL,
    status text NOT NULL DEFAULT 'queued',
    started_at timestamptz,
    completed_at timestamptz,
    duration_ms integer,
    input_ref jsonb NOT NULL DEFAULT '{}'::jsonb,
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    output_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_code text,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT postcall_model_runs_name_not_blank CHECK (btrim(model_name) <> ''),
    CONSTRAINT postcall_model_runs_role_valid CHECK (
        model_role IN (
            'audio_preprocess',
            'vad',
            'voice_state',
            'sound_event',
            'embedding',
            'reranker',
            'fusion'
        )
    ),
    CONSTRAINT postcall_model_runs_status_valid CHECK (
        status IN ('queued', 'running', 'succeeded', 'failed', 'skipped')
    ),
    CONSTRAINT postcall_model_runs_duration_non_negative CHECK (
        duration_ms IS NULL OR duration_ms >= 0
    ),
    CONSTRAINT postcall_model_runs_json_objects CHECK (
        jsonb_typeof(input_ref) = 'object'
        AND jsonb_typeof(metrics) = 'object'
        AND jsonb_typeof(output_summary) = 'object'
    )
);

CREATE INDEX IF NOT EXISTS idx_postcall_model_runs_job_role_created_at
    ON postcall_model_runs (postcall_job_id, model_role, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_postcall_model_runs_status_created_at
    ON postcall_model_runs (status, created_at DESC);

CREATE TABLE IF NOT EXISTS postcall_analysis_results (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    postcall_job_id uuid NOT NULL REFERENCES postcall_jobs(id) ON DELETE CASCADE,
    risk_level text NOT NULL DEFAULT 'unknown',
    need_attention boolean NOT NULL DEFAULT false,
    confidence numeric(5, 4),
    risk_types text[] NOT NULL DEFAULT ARRAY[]::text[],
    summary text NOT NULL DEFAULT '',
    recommended_actions text[] NOT NULL DEFAULT ARRAY[]::text[],
    model_versions jsonb NOT NULL DEFAULT '{}'::jsonb,
    audio_processing jsonb NOT NULL DEFAULT '{}'::jsonb,
    fusion_trace jsonb NOT NULL DEFAULT '{}'::jsonb,
    result_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT postcall_analysis_results_job_key UNIQUE (postcall_job_id),
    CONSTRAINT postcall_analysis_results_risk_level_valid CHECK (
        risk_level IN ('unknown', 'none', 'low', 'medium', 'high', 'critical')
    ),
    CONSTRAINT postcall_analysis_results_confidence_range CHECK (
        confidence IS NULL OR (confidence >= 0 AND confidence <= 1)
    ),
    CONSTRAINT postcall_analysis_results_json_objects CHECK (
        jsonb_typeof(model_versions) = 'object'
        AND jsonb_typeof(audio_processing) = 'object'
        AND jsonb_typeof(fusion_trace) = 'object'
        AND jsonb_typeof(result_payload) = 'object'
    )
);

CREATE INDEX IF NOT EXISTS idx_postcall_analysis_results_risk_created_at
    ON postcall_analysis_results (risk_level, created_at DESC);

CREATE TABLE IF NOT EXISTS postcall_timeline_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    postcall_job_id uuid NOT NULL REFERENCES postcall_jobs(id) ON DELETE CASCADE,
    analysis_result_id uuid REFERENCES postcall_analysis_results(id) ON DELETE SET NULL,
    start_sec numeric(10, 3) NOT NULL,
    end_sec numeric(10, 3) NOT NULL,
    event_type text NOT NULL,
    label text NOT NULL,
    label_zh text,
    label_en text,
    native_label text,
    score numeric(5, 4),
    severity text,
    model_name text,
    model_version text,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT postcall_timeline_time_valid CHECK (
        start_sec >= 0
        AND end_sec >= start_sec
    ),
    CONSTRAINT postcall_timeline_event_type_valid CHECK (
        event_type IN ('voice_state', 'sound_event', 'derived_risk', 'audio_quality', 'system')
    ),
    CONSTRAINT postcall_timeline_label_not_blank CHECK (btrim(label) <> ''),
    CONSTRAINT postcall_timeline_optional_text_not_blank CHECK (
        (label_zh IS NULL OR btrim(label_zh) <> '')
        AND (label_en IS NULL OR btrim(label_en) <> '')
        AND (native_label IS NULL OR btrim(native_label) <> '')
        AND (severity IS NULL OR btrim(severity) <> '')
        AND (model_name IS NULL OR btrim(model_name) <> '')
        AND (model_version IS NULL OR btrim(model_version) <> '')
    ),
    CONSTRAINT postcall_timeline_score_range CHECK (
        score IS NULL OR (score >= 0 AND score <= 1)
    ),
    CONSTRAINT postcall_timeline_evidence_object CHECK (jsonb_typeof(evidence) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_postcall_timeline_job_time
    ON postcall_timeline_events (postcall_job_id, start_sec, end_sec);

CREATE INDEX IF NOT EXISTS idx_postcall_timeline_type_label
    ON postcall_timeline_events (event_type, label);

CREATE TABLE IF NOT EXISTS postcall_evidence_segments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    postcall_job_id uuid NOT NULL REFERENCES postcall_jobs(id) ON DELETE CASCADE,
    analysis_result_id uuid REFERENCES postcall_analysis_results(id) ON DELETE SET NULL,
    segment_id text NOT NULL,
    start_sec numeric(10, 3) NOT NULL,
    end_sec numeric(10, 3) NOT NULL,
    risk_level text NOT NULL,
    reason text NOT NULL,
    recommended_action text,
    clip_uri text,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT postcall_evidence_segments_job_segment_key UNIQUE (postcall_job_id, segment_id),
    CONSTRAINT postcall_evidence_segments_segment_not_blank CHECK (btrim(segment_id) <> ''),
    CONSTRAINT postcall_evidence_segments_time_valid CHECK (
        start_sec >= 0
        AND end_sec >= start_sec
    ),
    CONSTRAINT postcall_evidence_segments_risk_level_valid CHECK (
        risk_level IN ('unknown', 'none', 'low', 'medium', 'high', 'critical')
    ),
    CONSTRAINT postcall_evidence_segments_reason_not_blank CHECK (btrim(reason) <> ''),
    CONSTRAINT postcall_evidence_segments_optional_text_not_blank CHECK (
        (recommended_action IS NULL OR btrim(recommended_action) <> '')
        AND (clip_uri IS NULL OR btrim(clip_uri) <> '')
    ),
    CONSTRAINT postcall_evidence_segments_payload_object CHECK (jsonb_typeof(payload) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_postcall_evidence_segments_job_time
    ON postcall_evidence_segments (postcall_job_id, start_sec, end_sec);

CREATE INDEX IF NOT EXISTS idx_postcall_evidence_segments_risk_created_at
    ON postcall_evidence_segments (risk_level, created_at DESC);

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_postcall_jobs_updated_at ON postcall_jobs;
CREATE TRIGGER trg_postcall_jobs_updated_at
BEFORE UPDATE ON postcall_jobs
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_postcall_analysis_results_updated_at ON postcall_analysis_results;
CREATE TRIGGER trg_postcall_analysis_results_updated_at
BEFORE UPDATE ON postcall_analysis_results
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();
