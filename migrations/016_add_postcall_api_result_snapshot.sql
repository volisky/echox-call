ALTER TABLE postcall_analysis_results
    ADD COLUMN IF NOT EXISTS api_result_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS api_result_version text NOT NULL DEFAULT 'postcall_job_result_v1',
    ADD COLUMN IF NOT EXISTS api_result_generated_at timestamptz;

UPDATE postcall_analysis_results
SET
    api_result_payload = result_payload,
    api_result_generated_at = COALESCE(updated_at, created_at, now())
WHERE api_result_payload = '{}'::jsonb
  AND result_payload <> '{}'::jsonb;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'postcall_analysis_results_api_payload_object'
    ) THEN
        ALTER TABLE postcall_analysis_results
            ADD CONSTRAINT postcall_analysis_results_api_payload_object
            CHECK (jsonb_typeof(api_result_payload) = 'object');
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'postcall_analysis_results_api_version_not_blank'
    ) THEN
        ALTER TABLE postcall_analysis_results
            ADD CONSTRAINT postcall_analysis_results_api_version_not_blank
            CHECK (btrim(api_result_version) <> '');
    END IF;
END;
$$;

COMMENT ON COLUMN postcall_analysis_results.api_result_payload IS '对外 GET /api/v1/postcall/jobs/{jobId} 返回的 data 部分快照；不包含 code、message、timestamp';
COMMENT ON COLUMN postcall_analysis_results.api_result_version IS '对外 API 结果快照结构版本，当前为 postcall_job_result_v1';
COMMENT ON COLUMN postcall_analysis_results.api_result_generated_at IS '对外 API 结果快照生成时间，由 worker 写入';
