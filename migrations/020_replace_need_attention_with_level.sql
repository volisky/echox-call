-- Replace the binary need_attention result with a three-level attention result.
-- External API snapshots now use level / levelName only.

ALTER TABLE postcall_analysis_results
    DROP CONSTRAINT IF EXISTS postcall_analysis_results_api_snapshot_for_rule;

ALTER TABLE postcall_analysis_results
    DROP CONSTRAINT IF EXISTS postcall_analysis_results_mode_risk_consistent;

ALTER TABLE postcall_analysis_results
    DROP CONSTRAINT IF EXISTS postcall_analysis_results_attention_level_valid;

ALTER TABLE postcall_analysis_results
    ADD COLUMN IF NOT EXISTS attention_level smallint,
    ADD COLUMN IF NOT EXISTS attention_level_name text;

UPDATE postcall_analysis_results
SET
    attention_level = CASE
        WHEN analysis_mode = 'rule_evaluated' THEN
            CASE
                WHEN (
                    CASE
                        WHEN jsonb_typeof(api_result_payload->'needAttention') = 'boolean'
                            THEN (api_result_payload->>'needAttention')::boolean
                        ELSE false
                    END
                )
                OR (
                    CASE
                        WHEN to_regclass('public.postcall_analysis_results') IS NOT NULL
                            THEN COALESCE(need_attention, false)
                        ELSE false
                    END
                )
                    THEN 1
                WHEN jsonb_typeof(api_result_payload->'insights') = 'array'
                    AND jsonb_array_length(api_result_payload->'insights') > 0
                    THEN 2
                ELSE 3
            END
        ELSE NULL
    END,
    attention_level_name = CASE
        WHEN analysis_mode = 'rule_evaluated' THEN
            CASE
                WHEN (
                    CASE
                        WHEN jsonb_typeof(api_result_payload->'needAttention') = 'boolean'
                            THEN (api_result_payload->>'needAttention')::boolean
                        ELSE false
                    END
                )
                OR COALESCE(need_attention, false)
                    THEN '需要关注'
                WHEN jsonb_typeof(api_result_payload->'insights') = 'array'
                    AND jsonb_array_length(api_result_payload->'insights') > 0
                    THEN '建议复核'
                ELSE '暂无明显线索'
            END
        ELSE NULL
    END;

UPDATE postcall_analysis_results
SET api_result_payload =
    (api_result_payload - 'needAttention')
    || jsonb_build_object(
        'level',
        attention_level,
        'levelName',
        attention_level_name
    )
WHERE analysis_mode = 'rule_evaluated'
  AND api_result_payload <> '{}'::jsonb;

ALTER TABLE postcall_analysis_results
    DROP COLUMN IF EXISTS need_attention;

ALTER TABLE postcall_analysis_results
    ADD CONSTRAINT postcall_analysis_results_attention_level_valid
    CHECK (
        (
            attention_level IS NULL
            AND attention_level_name IS NULL
        )
        OR (
            attention_level IN (1, 2, 3)
            AND attention_level_name = CASE attention_level
                WHEN 1 THEN '需要关注'
                WHEN 2 THEN '建议复核'
                WHEN 3 THEN '暂无明显线索'
            END
        )
    );

ALTER TABLE postcall_analysis_results
    ADD CONSTRAINT postcall_analysis_results_mode_risk_consistent
    CHECK (
        (
            analysis_mode = 'raw_model_outputs'
            AND risk_evaluated = false
            AND risk_level = 'unknown'
            AND attention_level IS NULL
            AND attention_level_name IS NULL
            AND confidence IS NULL
            AND cardinality(risk_types) = 0
            AND cardinality(recommended_actions) = 0
            AND fusion_trace = '{}'::jsonb
        )
        OR (
            analysis_mode <> 'raw_model_outputs'
            AND risk_evaluated = true
        )
    );

ALTER TABLE postcall_analysis_results
    ADD CONSTRAINT postcall_analysis_results_api_snapshot_for_rule
    CHECK (
        analysis_mode <> 'rule_evaluated'
        OR (
            risk_evaluated = true
            AND api_result_generated_at IS NOT NULL
            AND api_result_payload <> '{}'::jsonb
            AND api_result_payload ?& ARRAY[
                'jobId',
                'jjdh',
                'state',
                'level',
                'levelName',
                'timeline',
                'insights'
            ]
            AND jsonb_typeof(api_result_payload->'jobId') = 'string'
            AND jsonb_typeof(api_result_payload->'jjdh') = 'string'
            AND jsonb_typeof(api_result_payload->'state') = 'string'
            AND api_result_payload->>'state' = 'completed'
            AND jsonb_typeof(api_result_payload->'level') = 'number'
            AND (api_result_payload->>'level')::integer = attention_level
            AND jsonb_typeof(api_result_payload->'levelName') = 'string'
            AND api_result_payload->>'levelName' = attention_level_name
            AND jsonb_typeof(api_result_payload->'timeline') = 'array'
            AND jsonb_typeof(api_result_payload->'insights') = 'array'
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
                    'beats',
                    'wavlm',
                    'keySegments',
                    'confidenceLevel'
                ]
            )
        )
    );

COMMENT ON COLUMN postcall_analysis_results.attention_level IS '关注等级：1=需要关注，2=建议复核，3=暂无明显线索；仅规则线索层或后续融合阶段写入';
COMMENT ON COLUMN postcall_analysis_results.attention_level_name IS '关注等级中文名称：需要关注、建议复核、暂无明显线索；必须与 attention_level 一致';
COMMENT ON COLUMN postcall_analysis_results.api_result_payload IS '对外 GET /api/v1/postcall/jobs/{jobId} 返回的 data 部分快照，是当前 API 结果的唯一 JSON 快照；不包含 code、message、timestamp；规则线索结果必须包含 jobId、jjdh、state、level、levelName、timeline、insights';
COMMENT ON TABLE postcall_evidence_segments IS '报警音频规则证据片段表，用于保存 level=1 需要关注的线索，便于人工重点回听和解释';
