-- Remove legacy nested needAttention keys from saved API snapshots and evidence payloads.

ALTER TABLE postcall_analysis_results
    DROP CONSTRAINT IF EXISTS postcall_analysis_results_api_snapshot_for_rule;

UPDATE postcall_analysis_results
SET api_result_payload = jsonb_set(
    api_result_payload,
    '{insights}',
    COALESCE(
        (
            SELECT jsonb_agg(item.value - 'needAttention'::text ORDER BY item.ordinality)
            FROM jsonb_array_elements(api_result_payload->'insights')
                WITH ORDINALITY AS item(value, ordinality)
        ),
        '[]'::jsonb
    )
)
WHERE analysis_mode = 'rule_evaluated'
  AND jsonb_typeof(api_result_payload->'insights') = 'array';

UPDATE postcall_evidence_segments
SET payload = jsonb_set(
    payload,
    '{insight}',
    (payload->'insight')::jsonb - 'needAttention'::text
)
WHERE jsonb_typeof(payload->'insight') = 'object'
  AND payload->'insight' ? 'needAttention';

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
            AND NOT jsonb_path_exists(api_result_payload, '$.insights[*].needAttention')
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

COMMENT ON COLUMN postcall_analysis_results.api_result_payload IS '对外 GET /api/v1/postcall/jobs/{jobId} 返回的 data 部分快照，是当前 API 结果的唯一 JSON 快照；不包含 code、message、timestamp；规则线索结果必须包含 jobId、jjdh、state、level、levelName、timeline、insights，且 insights 内不包含 needAttention';
COMMENT ON COLUMN postcall_evidence_segments.payload IS '规则证据完整 JSON，保存规则版本、命中规则、证据片段和线索快照；线索快照不包含对外废弃的 needAttention 字段';
