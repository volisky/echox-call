CREATE TABLE IF NOT EXISTS postcall_timeline_segments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    postcall_job_id uuid NOT NULL REFERENCES postcall_jobs(id) ON DELETE CASCADE,
    analysis_result_id uuid REFERENCES postcall_analysis_results(id) ON DELETE SET NULL,
    segment_id text NOT NULL,
    start_sec numeric(10, 3) NOT NULL,
    end_sec numeric(10, 3) NOT NULL,
    sound_events jsonb NOT NULL DEFAULT '[]'::jsonb,
    voice_states jsonb NOT NULL DEFAULT '[]'::jsonb,
    voice_emotion_dimensions jsonb NOT NULL DEFAULT '{}'::jsonb,
    segment_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    internal_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT postcall_timeline_segments_job_segment_key UNIQUE (
        postcall_job_id,
        segment_id
    ),
    CONSTRAINT postcall_timeline_segments_segment_id_not_blank CHECK (
        btrim(segment_id) <> ''
    ),
    CONSTRAINT postcall_timeline_segments_time_valid CHECK (
        start_sec >= 0
        AND end_sec >= start_sec
    ),
    CONSTRAINT postcall_timeline_segments_json_valid CHECK (
        jsonb_typeof(sound_events) = 'array'
        AND jsonb_typeof(voice_states) = 'array'
        AND jsonb_typeof(voice_emotion_dimensions) = 'object'
        AND jsonb_typeof(segment_payload) = 'object'
        AND jsonb_typeof(internal_payload) = 'object'
    )
);

CREATE INDEX IF NOT EXISTS idx_postcall_timeline_segments_job_time
    ON postcall_timeline_segments (postcall_job_id, start_sec, end_sec);

CREATE INDEX IF NOT EXISTS idx_postcall_timeline_segments_result_time
    ON postcall_timeline_segments (analysis_result_id, start_sec, end_sec)
    WHERE analysis_result_id IS NOT NULL;

DROP TRIGGER IF EXISTS trg_postcall_timeline_segments_updated_at
    ON postcall_timeline_segments;

CREATE TRIGGER trg_postcall_timeline_segments_updated_at
BEFORE UPDATE ON postcall_timeline_segments
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'postcall_timeline_events'
          AND column_name = 'label'
    ) AND NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'postcall_timeline_events'
          AND column_name = 'event_code'
    ) THEN
        ALTER TABLE postcall_timeline_events
            RENAME COLUMN label TO event_code;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'postcall_timeline_events'
          AND column_name = 'label_zh'
    ) AND NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'postcall_timeline_events'
          AND column_name = 'event_name'
    ) THEN
        ALTER TABLE postcall_timeline_events
            RENAME COLUMN label_zh TO event_name;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'postcall_timeline_events'
          AND column_name = 'label_en'
    ) AND NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'postcall_timeline_events'
          AND column_name = 'event_name_en'
    ) THEN
        ALTER TABLE postcall_timeline_events
            RENAME COLUMN label_en TO event_name_en;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'postcall_timeline_label_not_blank'
    ) AND NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'postcall_timeline_event_code_not_blank'
    ) THEN
        ALTER TABLE postcall_timeline_events
            RENAME CONSTRAINT postcall_timeline_label_not_blank
            TO postcall_timeline_event_code_not_blank;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'postcall_timeline_optional_text_not_blank'
    ) AND NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'postcall_timeline_optional_text_valid'
    ) THEN
        ALTER TABLE postcall_timeline_events
            RENAME CONSTRAINT postcall_timeline_optional_text_not_blank
            TO postcall_timeline_optional_text_valid;
    END IF;
END;
$$;

ALTER TABLE postcall_timeline_events
    ADD COLUMN IF NOT EXISTS timeline_segment_id uuid
        REFERENCES postcall_timeline_segments(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS segment_id text,
    ADD COLUMN IF NOT EXISTS confidence_level text;

DO $$
BEGIN
    IF to_regclass('public.idx_postcall_timeline_type_label') IS NOT NULL
       AND to_regclass('public.idx_postcall_timeline_type_code') IS NULL THEN
        ALTER INDEX idx_postcall_timeline_type_label
            RENAME TO idx_postcall_timeline_type_code;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'postcall_timeline_segment_id_not_blank'
    ) THEN
        ALTER TABLE postcall_timeline_events
            ADD CONSTRAINT postcall_timeline_segment_id_not_blank
            CHECK (segment_id IS NULL OR btrim(segment_id) <> '');
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'postcall_timeline_confidence_level_valid'
    ) THEN
        ALTER TABLE postcall_timeline_events
            ADD CONSTRAINT postcall_timeline_confidence_level_valid
            CHECK (
                confidence_level IS NULL
                OR confidence_level IN ('low', 'medium', 'high')
            );
    END IF;
END;
$$;

CREATE INDEX IF NOT EXISTS idx_postcall_timeline_segment
    ON postcall_timeline_events (timeline_segment_id, event_type, score DESC)
    WHERE timeline_segment_id IS NOT NULL;

COMMENT ON TABLE postcall_timeline_segments IS '报警音频时间线片段表，对应对外 API 返回的 timeline 数组中的一个时间段';
COMMENT ON COLUMN postcall_timeline_segments.id IS '内部 UUID 主键';
COMMENT ON COLUMN postcall_timeline_segments.postcall_job_id IS '关联的报警音频分析任务内部 ID';
COMMENT ON COLUMN postcall_timeline_segments.analysis_result_id IS '关联的最终分析结果 ID，可为空';
COMMENT ON COLUMN postcall_timeline_segments.segment_id IS '时间线片段编号，对应 API timeline[].segmentId，同一任务内唯一';
COMMENT ON COLUMN postcall_timeline_segments.start_sec IS '时间线片段开始时间，单位秒，对应 API timeline[].startSec';
COMMENT ON COLUMN postcall_timeline_segments.end_sec IS '时间线片段结束时间，单位秒，对应 API timeline[].endSec';
COMMENT ON COLUMN postcall_timeline_segments.sound_events IS '声音事件数组，对应 API timeline[].soundEvents';
COMMENT ON COLUMN postcall_timeline_segments.voice_states IS '人声状态数组，对应 API timeline[].voiceStates';
COMMENT ON COLUMN postcall_timeline_segments.voice_emotion_dimensions IS '人声情绪连续维度，对应 API timeline[].voiceEmotionDimensions';
COMMENT ON COLUMN postcall_timeline_segments.segment_payload IS '与 API timeline 单个片段基本一致的完整 JSON';
COMMENT ON COLUMN postcall_timeline_segments.internal_payload IS '内部排查扩展 JSON，默认不对外返回';
COMMENT ON COLUMN postcall_timeline_segments.created_at IS '记录创建时间';
COMMENT ON COLUMN postcall_timeline_segments.updated_at IS '记录更新时间，由触发器自动刷新';

COMMENT ON TABLE postcall_timeline_events IS '报警音频时间线事件明细表，保存时间线片段背后的声音事件、人声状态、风险推断和音频质量事件';
COMMENT ON COLUMN postcall_timeline_events.timeline_segment_id IS '关联的时间线片段 ID，对应 postcall_timeline_segments.id';
COMMENT ON COLUMN postcall_timeline_events.segment_id IS '冗余保存时间线片段编号，便于直接排查';
COMMENT ON COLUMN postcall_timeline_events.event_code IS '事件编码，对应 API soundEvents[].code 或 voiceStates[].code';
COMMENT ON COLUMN postcall_timeline_events.event_name IS '事件中文名称，对应 API soundEvents[].name 或 voiceStates[].name';
COMMENT ON COLUMN postcall_timeline_events.event_name_en IS '事件英文名称，可用于内部展示或调试';
COMMENT ON COLUMN postcall_timeline_events.confidence_level IS '置信等级：low、medium、high';
COMMENT ON COLUMN postcall_timeline_events.evidence IS '内部事件证据 JSON，例如模型名、模型版本、原生标签、top labels、阈值、原始概率';
COMMENT ON COLUMN postcall_analysis_results.result_payload IS '完整结果 JSON，结构应与对外查询接口基本一致';
