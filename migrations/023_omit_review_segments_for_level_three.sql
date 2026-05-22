-- Omit external reviewSegments when the attention level means no significant signal.

ALTER TABLE postcall_analysis_results
    DROP CONSTRAINT IF EXISTS postcall_analysis_results_api_snapshot_for_rule;

UPDATE postcall_analysis_results
SET
    api_result_payload = api_result_payload - 'reviewSegments',
    updated_at = now()
WHERE analysis_mode = 'rule_evaluated'
  AND attention_level = 3
  AND api_result_payload ? 'reviewSegments';

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
                'levelName'
            ]
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
        )
    );

COMMENT ON COLUMN postcall_analysis_results.api_result_payload IS '对外 GET /api/v1/postcall/jobs/{jobId} 返回的 data 部分快照；规则线索结果必须包含 jobId、jjdh、state、level、levelName；仅 level=1/2 时包含 reviewSegments；完整模型时间线只保存在 postcall_timeline_segments';
