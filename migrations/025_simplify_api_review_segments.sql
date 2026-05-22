-- Simplify external API reviewSegments to time range plus result only.

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
    WHERE inner_result.analysis_mode = 'rule_evaluated'
      AND jsonb_typeof(inner_result.api_result_payload->'reviewSegments') = 'array'
    GROUP BY inner_result.id
) AS simplified
WHERE result.id = simplified.id;

COMMENT ON COLUMN postcall_analysis_results.api_result_payload IS '对外 GET /api/v1/postcall/jobs/{jobId} 返回的 data 部分快照；规则线索结果必须包含 jobId、jjdh、state、level、levelName；仅 level=1/2 时包含简化 reviewSegments，单项只返回 startSec、endSec、result；完整复核证据保存在 postcall_review_segments';
COMMENT ON TABLE postcall_review_segments IS '报警音频复核片段明细表，结构化保存 reviewSegment 的完整证据；对外 API 只返回 startSec、endSec、result';
COMMENT ON COLUMN postcall_review_segments.payload IS '完整 reviewSegment 证据 JSON 快照，包含规则码、原因、声音事件、人声状态和来源片段；对外 API 不直接返回该完整 JSON';
