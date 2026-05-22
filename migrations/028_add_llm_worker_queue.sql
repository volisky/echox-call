-- 028_add_llm_worker_queue.sql
-- 新增 LLM worker 独立队列，与音频 worker 并行处理文本信息；
-- 修改 api_result_payload 结构以支持新的综合返回格式。

-- 在 postcall_jobs 增加两列：
-- audio_completed_at: 音频 worker 完成时间；为 NULL 表示音频分析尚未完成
-- audio_analysis_data: 音频分析阶段的中间结果，由 try_mark_overall_completed 读取后写入 postcall_analysis_results
ALTER TABLE postcall_jobs
    ADD COLUMN IF NOT EXISTS audio_completed_at  timestamptz,
    ADD COLUMN IF NOT EXISTS audio_analysis_data jsonb NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN postcall_jobs.audio_completed_at IS
    '音频 worker 完成分析的时间戳；NULL 表示音频分析尚未完成。两个 worker 均完成后 state 才变为 completed。';

COMMENT ON COLUMN postcall_jobs.audio_analysis_data IS
    '音频 worker 完成后写入的中间结果 JSON，包含 attentionLevel、attentionLevelName、ruleVersion、matchedRuleCodes、modelVersions、audioProcessing、reviewSegments、fusionTrace；由 try_mark_overall_completed 读取并写入 postcall_analysis_results。';

-- LLM worker 独立队列表
CREATE TABLE IF NOT EXISTS postcall_llm_jobs (
    id              uuid        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    postcall_job_id uuid        NOT NULL REFERENCES postcall_jobs (id) ON DELETE CASCADE,
    job_id          text        NOT NULL,
    state           text        NOT NULL DEFAULT 'queued',
    attempt_count   integer     NOT NULL DEFAULT 0,
    max_attempts    integer     NOT NULL DEFAULT 3,
    locked_by       text,
    locked_at       timestamptz,
    locked_until    timestamptz,
    next_run_at     timestamptz NOT NULL DEFAULT now(),
    started_at      timestamptz,
    completed_at    timestamptz,
    failed_at       timestamptz,
    error_code      text,
    error_message   text,
    llm_model       text,
    llm_output      jsonb,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT postcall_llm_jobs_state_valid CHECK (
        state IN ('queued', 'processing', 'completed', 'failed')
    ),
    CONSTRAINT postcall_llm_jobs_one_per_audio_job UNIQUE (postcall_job_id)
);

CREATE INDEX IF NOT EXISTS postcall_llm_jobs_queued_idx
    ON postcall_llm_jobs (next_run_at ASC)
    WHERE state = 'queued';

COMMENT ON TABLE postcall_llm_jobs IS
    'LLM worker 任务队列：与音频 worker 并行运行，基于通话转写、警情内容、风险人员等文本信息生成综合分析结论。';
COMMENT ON COLUMN postcall_llm_jobs.state IS
    'LLM worker 任务状态：queued（待处理）/ processing（处理中）/ completed（完成）/ failed（失败）';
COMMENT ON COLUMN postcall_llm_jobs.llm_output IS
    'Claude API 结构化输出 JSON：包含 level、levelName、caseTypeSummary、highRiskAddressSummary、highRiskPersonSummary';
COMMENT ON COLUMN postcall_llm_jobs.llm_model IS
    '本次调用使用的 Claude 模型 ID';

-- 更新 api_result_payload 的 CHECK 约束以匹配新的综合返回格式
-- 旧格式: {jobId, jjdh, state, level, levelName, reviewSegments?}
-- 新格式: {jobId, jjdh, state, overallResult: {level, levelName, summary, voiceResult, inputSnapshot, riskPerson?}}
ALTER TABLE postcall_analysis_results
    DROP CONSTRAINT IF EXISTS postcall_analysis_results_api_snapshot_contract;

ALTER TABLE postcall_analysis_results
    ADD CONSTRAINT postcall_analysis_results_api_snapshot_contract CHECK (
        api_result_generated_at IS NOT NULL
        AND api_result_payload <> '{}'::jsonb
        AND api_result_payload ?& ARRAY['jobId', 'jjdh', 'state', 'overallResult']
        AND jsonb_typeof(api_result_payload->'jobId') = 'string'
        AND jsonb_typeof(api_result_payload->'jjdh') = 'string'
        AND jsonb_typeof(api_result_payload->'state') = 'string'
        AND api_result_payload->>'state' = 'completed'
        AND jsonb_typeof(api_result_payload->'overallResult') = 'object'
        AND (api_result_payload->'overallResult'->>'level')::integer = attention_level
        AND api_result_payload->'overallResult'->>'levelName' = attention_level_name
    );

COMMENT ON COLUMN postcall_analysis_results.attention_level IS
    '综合关注等级（由 LLM worker 确定）：1=需要关注，2=建议复核，3=暂无明显线索';
COMMENT ON COLUMN postcall_analysis_results.attention_level_name IS
    '综合关注等级中文名（由 LLM worker 确定）：需要关注、建议复核、暂无明显线索';
COMMENT ON COLUMN postcall_analysis_results.api_result_payload IS
    '对外 GET /api/v1/postcall/jobs/{jobId} 返回的 data 快照；新格式包含 jobId、jjdh、state、overallResult（含 level、levelName、summary、voiceResult、inputSnapshot、riskPerson）';

-- 允许在音频 worker 完成而 LLM worker 尚未完成时，先插入时间线/复核片段（analysis_result_id 暂为 NULL）
-- 待两个 worker 均完成后，by try_mark_overall_completed 统一关联 analysis_result_id
ALTER TABLE postcall_timeline_segments ALTER COLUMN analysis_result_id DROP NOT NULL;
ALTER TABLE postcall_review_segments ALTER COLUMN analysis_result_id DROP NOT NULL;
