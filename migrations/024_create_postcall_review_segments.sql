-- Store external reviewSegments in a dedicated relational table.

CREATE TABLE IF NOT EXISTS postcall_review_segments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    postcall_job_id uuid NOT NULL REFERENCES postcall_jobs(id) ON DELETE CASCADE,
    analysis_result_id uuid REFERENCES postcall_analysis_results(id) ON DELETE SET NULL,
    segment_id text NOT NULL,
    start_sec double precision NOT NULL,
    end_sec double precision NOT NULL,
    level integer NOT NULL,
    level_name text NOT NULL,
    title text NOT NULL,
    reason text NOT NULL,
    confidence text NOT NULL,
    matched_rule_codes jsonb NOT NULL DEFAULT '[]'::jsonb,
    audio_events jsonb NOT NULL DEFAULT '[]'::jsonb,
    voice_states jsonb NOT NULL DEFAULT '[]'::jsonb,
    source_segments jsonb NOT NULL DEFAULT '[]'::jsonb,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT postcall_review_segments_job_segment_key UNIQUE (
        postcall_job_id,
        segment_id
    ),
    CONSTRAINT postcall_review_segments_segment_id_not_blank CHECK (
        btrim(segment_id) <> ''
    ),
    CONSTRAINT postcall_review_segments_time_valid CHECK (
        start_sec >= 0
        AND end_sec >= start_sec
    ),
    CONSTRAINT postcall_review_segments_level_valid CHECK (
        level IN (1, 2)
        AND (
            (level = 1 AND level_name = '需要关注')
            OR (level = 2 AND level_name = '建议复核')
        )
    ),
    CONSTRAINT postcall_review_segments_required_text_not_blank CHECK (
        btrim(level_name) <> ''
        AND btrim(title) <> ''
        AND btrim(reason) <> ''
    ),
    CONSTRAINT postcall_review_segments_confidence_valid CHECK (
        confidence IN ('low', 'medium', 'high')
    ),
    CONSTRAINT postcall_review_segments_json_valid CHECK (
        jsonb_typeof(matched_rule_codes) = 'array'
        AND jsonb_typeof(audio_events) = 'array'
        AND jsonb_typeof(voice_states) = 'array'
        AND jsonb_typeof(source_segments) = 'array'
        AND jsonb_typeof(payload) = 'object'
    )
);

CREATE INDEX IF NOT EXISTS idx_postcall_review_segments_job_time
    ON postcall_review_segments (postcall_job_id, start_sec, end_sec);

CREATE INDEX IF NOT EXISTS idx_postcall_review_segments_result_time
    ON postcall_review_segments (analysis_result_id, start_sec, end_sec)
    WHERE analysis_result_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_postcall_review_segments_level_created_at
    ON postcall_review_segments (level, created_at DESC);

DROP TRIGGER IF EXISTS trg_postcall_review_segments_updated_at
    ON postcall_review_segments;

CREATE TRIGGER trg_postcall_review_segments_updated_at
BEFORE UPDATE ON postcall_review_segments
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

INSERT INTO postcall_review_segments (
    postcall_job_id,
    analysis_result_id,
    segment_id,
    start_sec,
    end_sec,
    level,
    level_name,
    title,
    reason,
    confidence,
    matched_rule_codes,
    audio_events,
    voice_states,
    source_segments,
    payload
)
SELECT
    result.postcall_job_id,
    result.id,
    review_segment.value->>'segmentId',
    (review_segment.value->>'startSec')::double precision,
    (review_segment.value->>'endSec')::double precision,
    result.attention_level,
    result.attention_level_name,
    review_segment.value->>'title',
    review_segment.value->>'reason',
    review_segment.value->>'confidence',
    COALESCE(review_segment.value->'matchedRuleCodes', '[]'::jsonb),
    COALESCE(review_segment.value->'audioEvents', '[]'::jsonb),
    COALESCE(review_segment.value->'voiceStates', '[]'::jsonb),
    COALESCE(review_segment.value->'sourceSegments', '[]'::jsonb),
    review_segment.value
FROM postcall_analysis_results AS result
CROSS JOIN LATERAL jsonb_array_elements(result.api_result_payload->'reviewSegments')
    AS review_segment(value)
WHERE result.analysis_mode = 'rule_evaluated'
  AND result.attention_level IN (1, 2)
  AND jsonb_typeof(result.api_result_payload->'reviewSegments') = 'array'
ON CONFLICT (postcall_job_id, segment_id) DO UPDATE
SET
    analysis_result_id = EXCLUDED.analysis_result_id,
    start_sec = EXCLUDED.start_sec,
    end_sec = EXCLUDED.end_sec,
    level = EXCLUDED.level,
    level_name = EXCLUDED.level_name,
    title = EXCLUDED.title,
    reason = EXCLUDED.reason,
    confidence = EXCLUDED.confidence,
    matched_rule_codes = EXCLUDED.matched_rule_codes,
    audio_events = EXCLUDED.audio_events,
    voice_states = EXCLUDED.voice_states,
    source_segments = EXCLUDED.source_segments,
    payload = EXCLUDED.payload,
    updated_at = now();

COMMENT ON TABLE postcall_review_segments IS '报警音频对外复核片段表，逐条保存 GET 接口 data.reviewSegments 中的复核片段；仅 level=1/2 时写入';
COMMENT ON COLUMN postcall_review_segments.id IS '内部 UUID 主键';
COMMENT ON COLUMN postcall_review_segments.postcall_job_id IS '关联的报警音频分析任务内部 ID';
COMMENT ON COLUMN postcall_review_segments.analysis_result_id IS '关联的最终分析结果 ID，可为空';
COMMENT ON COLUMN postcall_review_segments.segment_id IS '复核片段编号，对应 API reviewSegments[].segmentId，同一任务内唯一';
COMMENT ON COLUMN postcall_review_segments.start_sec IS '复核片段开始时间，单位秒，对应 API reviewSegments[].startSec';
COMMENT ON COLUMN postcall_review_segments.end_sec IS '复核片段结束时间，单位秒，对应 API reviewSegments[].endSec';
COMMENT ON COLUMN postcall_review_segments.level IS '关注等级：1=需要关注，2=建议复核；level=3 不写入本表';
COMMENT ON COLUMN postcall_review_segments.level_name IS '关注等级中文名：需要关注或建议复核';
COMMENT ON COLUMN postcall_review_segments.title IS '复核片段标题，对应 API reviewSegments[].title';
COMMENT ON COLUMN postcall_review_segments.reason IS '复核原因，对应 API reviewSegments[].reason，来自规则模板';
COMMENT ON COLUMN postcall_review_segments.confidence IS '线索置信度分档：low、medium、high';
COMMENT ON COLUMN postcall_review_segments.matched_rule_codes IS '命中的规则编码数组，对应 API reviewSegments[].matchedRuleCodes';
COMMENT ON COLUMN postcall_review_segments.audio_events IS '支撑该片段的声音事件证据数组，对应 API reviewSegments[].audioEvents';
COMMENT ON COLUMN postcall_review_segments.voice_states IS '支撑该片段的人声状态证据数组，对应 API reviewSegments[].voiceStates';
COMMENT ON COLUMN postcall_review_segments.source_segments IS '证据来源的内部模型时间窗编号数组，对应 API reviewSegments[].sourceSegments';
COMMENT ON COLUMN postcall_review_segments.payload IS '完整 reviewSegment JSON 快照，必须与 API reviewSegments 单项结构一致';
COMMENT ON COLUMN postcall_review_segments.created_at IS '记录创建时间';
COMMENT ON COLUMN postcall_review_segments.updated_at IS '记录更新时间，由触发器自动刷新';
