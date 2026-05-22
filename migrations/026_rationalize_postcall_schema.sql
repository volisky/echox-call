-- Rationalize postcall storage around the current API contract.
-- Keep one source of truth for each concern:
-- jobs / audio assets / model runs / analysis result / model timeline / review segments.

DROP TABLE IF EXISTS postcall_evidence_segments;
DROP TABLE IF EXISTS postcall_timeline_events;
DROP TABLE IF EXISTS postcall_job_duplicate_submissions;

ALTER TABLE postcall_analysis_results
    DROP CONSTRAINT IF EXISTS postcall_analysis_results_api_snapshot_for_rule,
    DROP CONSTRAINT IF EXISTS postcall_analysis_results_json_objects,
    DROP CONSTRAINT IF EXISTS postcall_analysis_results_mode_risk_consistent,
    DROP CONSTRAINT IF EXISTS postcall_analysis_results_mode_valid,
    DROP CONSTRAINT IF EXISTS postcall_analysis_results_rule_v1_neutral,
    DROP CONSTRAINT IF EXISTS postcall_analysis_results_risk_level_valid,
    DROP CONSTRAINT IF EXISTS postcall_analysis_results_confidence_range;

ALTER TABLE postcall_analysis_results
    ADD COLUMN IF NOT EXISTS rule_version text,
    ADD COLUMN IF NOT EXISTS matched_rule_codes jsonb NOT NULL DEFAULT '[]'::jsonb;

UPDATE postcall_analysis_results
SET
    rule_version = COALESCE(NULLIF(fusion_trace->>'ruleVersion', ''), rule_version, 'unknown'),
    matched_rule_codes = CASE
        WHEN jsonb_typeof(fusion_trace->'matchedRuleCodes') = 'array' THEN fusion_trace->'matchedRuleCodes'
        WHEN jsonb_typeof(matched_rule_codes) = 'array' THEN matched_rule_codes
        ELSE '[]'::jsonb
    END
WHERE fusion_trace IS NOT NULL;

UPDATE postcall_analysis_results
SET rule_version = 'unknown'
WHERE rule_version IS NULL OR btrim(rule_version) = '';

UPDATE postcall_analysis_results AS result
SET
    api_result_payload = jsonb_set(
        result.api_result_payload,
        '{reviewSegments}',
        simplified.review_segments,
        true
    ),
    updated_at = now()
FROM (
    SELECT
        inner_result.id,
        jsonb_agg(
            jsonb_build_object(
                'startSec',
                review_segment.value->'startSec',
                'endSec',
                review_segment.value->'endSec',
                'result',
                COALESCE(review_segment.value->>'result', review_segment.value->>'title')
            )
            ORDER BY review_segment.ordinality
        ) AS review_segments
    FROM postcall_analysis_results AS inner_result
    CROSS JOIN LATERAL jsonb_array_elements(inner_result.api_result_payload->'reviewSegments')
        WITH ORDINALITY AS review_segment(value, ordinality)
    WHERE jsonb_typeof(inner_result.api_result_payload->'reviewSegments') = 'array'
    GROUP BY inner_result.id
) AS simplified
WHERE result.id = simplified.id;

ALTER TABLE postcall_analysis_results
    ALTER COLUMN rule_version SET NOT NULL,
    DROP COLUMN IF EXISTS risk_level,
    DROP COLUMN IF EXISTS confidence,
    DROP COLUMN IF EXISTS risk_types,
    DROP COLUMN IF EXISTS summary,
    DROP COLUMN IF EXISTS recommended_actions,
    DROP COLUMN IF EXISTS analysis_mode,
    DROP COLUMN IF EXISTS risk_evaluated,
    DROP COLUMN IF EXISTS fusion_trace;

ALTER TABLE postcall_analysis_results
    ADD CONSTRAINT postcall_analysis_results_rule_version_not_blank CHECK (
        btrim(rule_version) <> ''
    ),
    ADD CONSTRAINT postcall_analysis_results_matched_rule_codes_array CHECK (
        jsonb_typeof(matched_rule_codes) = 'array'
    ),
    ADD CONSTRAINT postcall_analysis_results_json_objects CHECK (
        jsonb_typeof(model_versions) = 'object'
        AND jsonb_typeof(audio_processing) = 'object'
        AND jsonb_typeof(api_result_payload) = 'object'
    ),
    ADD CONSTRAINT postcall_analysis_results_api_snapshot_contract CHECK (
        api_result_generated_at IS NOT NULL
        AND api_result_payload <> '{}'::jsonb
        AND api_result_payload ?& ARRAY['jobId', 'jjdh', 'state', 'level', 'levelName']
        AND jsonb_typeof(api_result_payload->'jobId') = 'string'
        AND jsonb_typeof(api_result_payload->'jjdh') = 'string'
        AND jsonb_typeof(api_result_payload->'state') = 'string'
        AND api_result_payload->>'state' = 'completed'
        AND jsonb_typeof(api_result_payload->'level') = 'number'
        AND (api_result_payload->>'level')::integer = attention_level
        AND jsonb_typeof(api_result_payload->'levelName') = 'string'
        AND api_result_payload->>'levelName' = attention_level_name
        AND (
            (
                attention_level = 3
                AND NOT (api_result_payload ? 'reviewSegments')
            )
            OR (
                attention_level IN (1, 2)
                AND api_result_payload ? 'reviewSegments'
                AND jsonb_typeof(api_result_payload->'reviewSegments') = 'array'
                AND jsonb_array_length(api_result_payload->'reviewSegments') > 0
                AND NOT jsonb_path_exists(api_result_payload, '$.reviewSegments[*].segmentId')
                AND NOT jsonb_path_exists(api_result_payload, '$.reviewSegments[*].title')
                AND NOT jsonb_path_exists(api_result_payload, '$.reviewSegments[*].reason')
                AND NOT jsonb_path_exists(api_result_payload, '$.reviewSegments[*].confidence')
                AND NOT jsonb_path_exists(api_result_payload, '$.reviewSegments[*].matchedRuleCodes')
                AND NOT jsonb_path_exists(api_result_payload, '$.reviewSegments[*].audioEvents')
                AND NOT jsonb_path_exists(api_result_payload, '$.reviewSegments[*].voiceStates')
                AND NOT jsonb_path_exists(api_result_payload, '$.reviewSegments[*].sourceSegments')
            )
        )
        AND NOT (
            api_result_payload ?| ARRAY[
                'analysis',
                'audio',
                'riskLevel',
                'analysisMode',
                'riskEvaluated',
                'needAttention',
                'attentionStatus',
                'attentionStatusName',
                'timeline',
                'insights',
                'beats',
                'wavlm',
                'keySegments',
                'confidenceLevel'
            ]
        )
    );

ALTER TABLE postcall_timeline_segments
    DROP CONSTRAINT IF EXISTS postcall_timeline_segments_payload_contract,
    DROP CONSTRAINT IF EXISTS postcall_timeline_segments_json_valid;

ALTER TABLE postcall_timeline_segments
    DROP COLUMN IF EXISTS segment_payload;

ALTER TABLE postcall_timeline_segments
    ADD CONSTRAINT postcall_timeline_segments_json_valid CHECK (
        jsonb_typeof(audio_event_scores) = 'array'
        AND jsonb_typeof(voice_emotion_scores) = 'array'
        AND jsonb_typeof(voice_detailed_scores) = 'array'
        AND jsonb_typeof(voice_emotion_dimensions) = 'object'
        AND jsonb_typeof(internal_payload) = 'object'
    );

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'postcall_review_segments'
          AND column_name = 'title'
    )
    AND NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'postcall_review_segments'
          AND column_name = 'result'
    ) THEN
        ALTER TABLE postcall_review_segments RENAME COLUMN title TO result;
    END IF;
END $$;

ALTER TABLE postcall_review_segments
    DROP CONSTRAINT IF EXISTS postcall_review_segments_required_text_not_blank;

ALTER TABLE postcall_review_segments
    ADD CONSTRAINT postcall_review_segments_required_text_not_blank CHECK (
        btrim(level_name) <> ''
        AND btrim(result) <> ''
        AND btrim(reason) <> ''
    );

UPDATE postcall_review_segments
SET payload = jsonb_set(
    payload,
    '{result}',
    to_jsonb(COALESCE(payload->>'result', result)),
    true
)
WHERE jsonb_typeof(payload) = 'object';

COMMENT ON TABLE postcall_jobs IS '报警音频分析任务主表，保存一次接警音频任务的请求、状态、重试锁和客户端隔离信息';
COMMENT ON COLUMN postcall_jobs.duplicate_count IS '同一 jjdh 重复提交次数；重复提交会重置任务并清理旧分析输出，不再单独写重复提交审计表';

COMMENT ON TABLE postcall_analysis_results IS '报警音频分析结果表，每个任务保存一条当前有效结果；api_result_payload 是 GET 接口 data 的数据库快照';
COMMENT ON COLUMN postcall_analysis_results.attention_level IS '关注等级：1=需要关注，2=建议复核，3=暂无明显线索';
COMMENT ON COLUMN postcall_analysis_results.attention_level_name IS '关注等级中文名：需要关注、建议复核、暂无明显线索';
COMMENT ON COLUMN postcall_analysis_results.rule_version IS '规则线索层版本，例如 postcall_attention_rules_v3';
COMMENT ON COLUMN postcall_analysis_results.matched_rule_codes IS '本次任务命中的规则编码数组，用于排查规则来源';
COMMENT ON COLUMN postcall_analysis_results.model_versions IS '本次分析使用的模型版本摘要 JSON';
COMMENT ON COLUMN postcall_analysis_results.audio_processing IS '音频下载、标准化、切片、profile 和模型片段数量等处理摘要 JSON';
COMMENT ON COLUMN postcall_analysis_results.api_result_payload IS '对外 GET /api/v1/postcall/jobs/{jobId} 返回的 data 快照；包含 jobId、jjdh、state、level、levelName，仅 level=1/2 时包含简化 reviewSegments';
COMMENT ON COLUMN postcall_analysis_results.api_result_version IS 'API 结果快照结构版本';
COMMENT ON COLUMN postcall_analysis_results.api_result_generated_at IS 'API 结果快照生成时间';

COMMENT ON TABLE postcall_timeline_segments IS '报警音频模型时间线片段表，保存声音事件和人声状态的结构化模型输出；不直接作为外部 GET 返回';
COMMENT ON COLUMN postcall_timeline_segments.audio_event_scores IS '声音事件 Top-K 分数数组；单项包含 eventNameEn、eventNameZh、score';
COMMENT ON COLUMN postcall_timeline_segments.voice_emotion_scores IS '人声状态 9 类情绪分数数组；单项包含 emotionNameEn、emotionNameZh、score';
COMMENT ON COLUMN postcall_timeline_segments.voice_detailed_scores IS 'WavLM 17 类细粒度分数数组，仅内部保存，当前无可靠标签名，不对外返回';
COMMENT ON COLUMN postcall_timeline_segments.voice_emotion_dimensions IS '人声连续维度 JSON，包含 arousal、valence、dominance，用于规则组合和内部排查';
COMMENT ON COLUMN postcall_timeline_segments.internal_payload IS '内部排查 JSON，保存模型名、版本、窗口参数、BEATs 全量 527 类分数等不对外展示的信息';

COMMENT ON TABLE postcall_review_segments IS '报警音频复核片段表，保存规则层筛出的重点复核片段完整证据；对外 API 只返回 startSec、endSec、result';
COMMENT ON COLUMN postcall_review_segments.result IS '复核片段结果短语，对应 API reviewSegments[].result，例如 疑似哭泣';
COMMENT ON COLUMN postcall_review_segments.reason IS '复核原因，来自规则模板，对外当前不返回';
COMMENT ON COLUMN postcall_review_segments.payload IS '完整 reviewSegment 证据 JSON 快照，包含规则码、原因、声音事件、人声状态和来源片段；对外 API 当前不直接返回';
