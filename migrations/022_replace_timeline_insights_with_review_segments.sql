-- Replace external timeline + insights payload with reviewSegments.
-- Full model timeline remains in postcall_timeline_segments for debugging and rule recomputation.

ALTER TABLE postcall_analysis_results
    DROP CONSTRAINT IF EXISTS postcall_analysis_results_api_snapshot_for_rule;

UPDATE postcall_analysis_results
SET api_result_payload =
    (api_result_payload - 'timeline' - 'insights')
    || jsonb_build_object(
        'reviewSegments',
        COALESCE(
            (
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'segmentId',
                        format('review_%s', lpad(item.ordinality::text, 6, '0')),
                        'startSec',
                        item.value->'startSec',
                        'endSec',
                        item.value->'endSec',
                        'title',
                        item.value->'insightName',
                        'reason',
                        item.value->'reason',
                        'confidence',
                        item.value->'confidence',
                        'matchedRuleCodes',
                        COALESCE(item.value->'matchedRuleCodes', '[]'::jsonb),
                        'audioEvents',
                        COALESCE(
                            (
                                SELECT jsonb_agg(
                                    DISTINCT jsonb_build_object(
                                        'nameEn',
                                        evidence.value->'nameEn',
                                        'nameZh',
                                        evidence.value->'nameZh',
                                        'score',
                                        evidence.value->'score'
                                    )
                                )
                                FROM jsonb_array_elements(
                                    CASE
                                        WHEN jsonb_typeof(item.value->'evidence') = 'array'
                                            THEN item.value->'evidence'
                                        ELSE '[]'::jsonb
                                    END
                                ) AS evidence(value)
                                WHERE evidence.value->>'sourceField' = 'audioEventScores'
                            ),
                            '[]'::jsonb
                        ),
                        'voiceStates',
                        COALESCE(
                            (
                                SELECT jsonb_agg(
                                    DISTINCT jsonb_build_object(
                                        'nameEn',
                                        evidence.value->'nameEn',
                                        'nameZh',
                                        evidence.value->'nameZh',
                                        'score',
                                        evidence.value->'score'
                                    )
                                )
                                FROM jsonb_array_elements(
                                    CASE
                                        WHEN jsonb_typeof(item.value->'evidence') = 'array'
                                            THEN item.value->'evidence'
                                        ELSE '[]'::jsonb
                                    END
                                ) AS evidence(value)
                                WHERE evidence.value->>'sourceField' IN (
                                    'voiceEmotionScores',
                                    'voiceEmotionDimensions'
                                )
                            ),
                            '[]'::jsonb
                        ),
                        'sourceSegments',
                        COALESCE(
                            (
                                SELECT jsonb_agg(to_jsonb(source.segment_id))
                                FROM (
                                    SELECT DISTINCT evidence.value->>'segmentId' AS segment_id
                                    FROM jsonb_array_elements(
                                        CASE
                                            WHEN jsonb_typeof(item.value->'evidence') = 'array'
                                                THEN item.value->'evidence'
                                            ELSE '[]'::jsonb
                                        END
                                    ) AS evidence(value)
                                    WHERE COALESCE(evidence.value->>'segmentId', '') <> ''
                                    ORDER BY segment_id
                                ) AS source
                            ),
                            '[]'::jsonb
                        )
                    )
                    ORDER BY item.ordinality
                )
                FROM jsonb_array_elements(
                    CASE
                        WHEN jsonb_typeof(api_result_payload->'insights') = 'array'
                            THEN api_result_payload->'insights'
                        ELSE '[]'::jsonb
                    END
                ) WITH ORDINALITY AS item(value, ordinality)
            ),
            '[]'::jsonb
        )
    )
WHERE analysis_mode = 'rule_evaluated'
  AND api_result_payload <> '{}'::jsonb;

UPDATE postcall_evidence_segments
SET payload =
    (payload - 'insight')
    || jsonb_build_object(
        'reviewSegment',
        COALESCE(
            payload->'reviewSegment',
            jsonb_build_object(
                'segmentId',
                replace(segment_id, 'insight_', 'review_'),
                'startSec',
                start_sec,
                'endSec',
                end_sec,
                'title',
                payload->'insight'->'insightName',
                'reason',
                reason,
                'confidence',
                payload->'insight'->'confidence',
                'matchedRuleCodes',
                COALESCE(payload->'matchedRuleCodes', '[]'::jsonb),
                'audioEvents',
                COALESCE(
                    (
                        SELECT jsonb_agg(
                            DISTINCT jsonb_build_object(
                                'nameEn',
                                evidence.value->'nameEn',
                                'nameZh',
                                evidence.value->'nameZh',
                                'score',
                                evidence.value->'score'
                            )
                        )
                        FROM jsonb_array_elements(
                            CASE
                                WHEN jsonb_typeof(payload->'evidence') = 'array'
                                    THEN payload->'evidence'
                                ELSE '[]'::jsonb
                            END
                        ) AS evidence(value)
                        WHERE evidence.value->>'sourceField' = 'audioEventScores'
                    ),
                    '[]'::jsonb
                ),
                'voiceStates',
                COALESCE(
                    (
                        SELECT jsonb_agg(
                            DISTINCT jsonb_build_object(
                                'nameEn',
                                evidence.value->'nameEn',
                                'nameZh',
                                evidence.value->'nameZh',
                                'score',
                                evidence.value->'score'
                            )
                        )
                        FROM jsonb_array_elements(
                            CASE
                                WHEN jsonb_typeof(payload->'evidence') = 'array'
                                    THEN payload->'evidence'
                                ELSE '[]'::jsonb
                            END
                        ) AS evidence(value)
                        WHERE evidence.value->>'sourceField' IN (
                            'voiceEmotionScores',
                            'voiceEmotionDimensions'
                        )
                    ),
                    '[]'::jsonb
                ),
                'sourceSegments',
                COALESCE(
                    (
                        SELECT jsonb_agg(to_jsonb(source.segment_id))
                        FROM (
                            SELECT DISTINCT evidence.value->>'segmentId' AS segment_id
                            FROM jsonb_array_elements(
                                CASE
                                    WHEN jsonb_typeof(payload->'evidence') = 'array'
                                        THEN payload->'evidence'
                                    ELSE '[]'::jsonb
                                END
                            ) AS evidence(value)
                            WHERE COALESCE(evidence.value->>'segmentId', '') <> ''
                            ORDER BY segment_id
                        ) AS source
                    ),
                    '[]'::jsonb
                )
            )
        )
    )
WHERE payload ? 'insight'
   OR payload ? 'reviewSegment';

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
                'reviewSegments'
            ]
            AND jsonb_typeof(api_result_payload->'jobId') = 'string'
            AND jsonb_typeof(api_result_payload->'jjdh') = 'string'
            AND jsonb_typeof(api_result_payload->'state') = 'string'
            AND api_result_payload->>'state' = 'completed'
            AND jsonb_typeof(api_result_payload->'level') = 'number'
            AND (api_result_payload->>'level')::integer = attention_level
            AND jsonb_typeof(api_result_payload->'levelName') = 'string'
            AND api_result_payload->>'levelName' = attention_level_name
            AND jsonb_typeof(api_result_payload->'reviewSegments') = 'array'
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

COMMENT ON COLUMN postcall_analysis_results.api_result_payload IS '对外 GET /api/v1/postcall/jobs/{jobId} 返回的 data 部分快照；规则线索结果必须包含 jobId、jjdh、state、level、levelName、reviewSegments；完整模型时间线只保存在 postcall_timeline_segments';
COMMENT ON COLUMN postcall_evidence_segments.payload IS '规则证据完整 JSON，保存规则版本、命中规则、证据片段和 reviewSegment 快照';
