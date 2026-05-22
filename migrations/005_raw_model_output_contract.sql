DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'postcall_timeline_segments'
          AND column_name = 'sound_events'
    ) AND NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'postcall_timeline_segments'
          AND column_name = 'audio_event_scores'
    ) THEN
        ALTER TABLE postcall_timeline_segments
            RENAME COLUMN sound_events TO audio_event_scores;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'postcall_timeline_segments'
          AND column_name = 'voice_states'
    ) AND NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'postcall_timeline_segments'
          AND column_name = 'voice_emotion_scores'
    ) THEN
        ALTER TABLE postcall_timeline_segments
            RENAME COLUMN voice_states TO voice_emotion_scores;
    END IF;
END;
$$;

ALTER TABLE postcall_timeline_segments
    ADD COLUMN IF NOT EXISTS voice_detailed_scores jsonb
        NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE postcall_timeline_segments
    DROP CONSTRAINT IF EXISTS postcall_timeline_segments_json_valid;

ALTER TABLE postcall_timeline_segments
    ADD CONSTRAINT postcall_timeline_segments_json_valid CHECK (
        jsonb_typeof(audio_event_scores) = 'array'
        AND jsonb_typeof(voice_emotion_scores) = 'array'
        AND jsonb_typeof(voice_detailed_scores) = 'array'
        AND jsonb_typeof(voice_emotion_dimensions) = 'object'
        AND jsonb_typeof(segment_payload) = 'object'
        AND jsonb_typeof(internal_payload) = 'object'
    );

ALTER TABLE postcall_timeline_events
    DROP CONSTRAINT IF EXISTS postcall_timeline_event_type_valid;

ALTER TABLE postcall_timeline_events
    ADD CONSTRAINT postcall_timeline_event_type_valid CHECK (
        event_type IN (
            'audio_event_score',
            'voice_emotion_score',
            'voice_emotion_dimension',
            'voice_detailed_score',
            'sound_event',
            'voice_state',
            'derived_risk',
            'audio_quality',
            'system'
        )
    );

ALTER TABLE postcall_model_runs
    DROP CONSTRAINT IF EXISTS postcall_model_runs_role_valid;

ALTER TABLE postcall_model_runs
    ADD CONSTRAINT postcall_model_runs_role_valid CHECK (
        model_role IN (
            'audio_preprocess',
            'vad',
            'audio_event',
            'voice_emotion',
            'voice_emotion_detail',
            'embedding',
            'reranker',
            'fusion',
            'sound_event',
            'voice_state'
        )
    );

COMMENT ON TABLE postcall_timeline_segments IS '报警音频时间线片段表，当前用于保存对外 API 返回的原始模型输出片段';
COMMENT ON COLUMN postcall_timeline_segments.audio_event_scores IS 'BEATs 原始声音事件分数数组，对应 API timeline[].audioEventScores，可保存 top-k 原始分数';
COMMENT ON COLUMN postcall_timeline_segments.voice_emotion_scores IS 'WavLM 9 类情绪原始分数数组，对应 API timeline[].voiceEmotionScores';
COMMENT ON COLUMN postcall_timeline_segments.voice_detailed_scores IS 'WavLM 17 类细粒度原始分数数组，对应 API timeline[].voiceDetailedScores，标签未确认时只保存 index 和 score';
COMMENT ON COLUMN postcall_timeline_segments.voice_emotion_dimensions IS 'WavLM arousal、valence、dominance 连续维度原始输出，对应 API timeline[].voiceEmotionDimensions';
COMMENT ON COLUMN postcall_timeline_segments.segment_payload IS '与 API timeline 单个片段基本一致的原始模型输出 JSON';
COMMENT ON COLUMN postcall_timeline_segments.internal_payload IS '内部排查扩展 JSON，可保存完整向量、top-k、窗口参数和模型加载信息，默认不对外返回';

COMMENT ON TABLE postcall_timeline_events IS '报警音频时间线原始输出明细表，保存每个时间窗内模型原始分数和内部证据；当前不做风险规则判断';
COMMENT ON COLUMN postcall_timeline_events.event_type IS '原始输出类型：audio_event_score、voice_emotion_score、voice_emotion_dimension、voice_detailed_score；其他类型为后续预留';
COMMENT ON COLUMN postcall_timeline_events.event_code IS '原始输出编码，例如 AudioSet MID、WavLM emotion label、维度名或 17 类细粒度 index';
COMMENT ON COLUMN postcall_timeline_events.event_name IS '原始输出标签名，例如 Screaming、Fear、arousal；无可靠标签时为空';
COMMENT ON COLUMN postcall_timeline_events.event_name_en IS '原始英文标签名，当前原始输出模式下可与 event_name 保持一致';
COMMENT ON COLUMN postcall_timeline_events.native_label IS '模型原生标签或原始 index，例如 AudioSet label、WavLM emotion label、detailed_index_0';
COMMENT ON COLUMN postcall_timeline_events.score IS '模型原始分数或归一化概率，范围 0 到 1';
COMMENT ON COLUMN postcall_timeline_events.confidence_level IS '后续阈值分档预留字段，原始输出模式不写入';
COMMENT ON COLUMN postcall_timeline_events.severity IS '后续风险规则预留字段，原始输出模式不写入';
COMMENT ON COLUMN postcall_timeline_events.evidence IS '内部证据 JSON，可保存模型名、模型版本、窗口参数、完整 top-k 或原始向量摘要';

COMMENT ON COLUMN postcall_model_runs.model_role IS '模型或处理器职责：当前原始输出模式主要使用 audio_preprocess、vad、audio_event、voice_emotion、voice_emotion_detail';
COMMENT ON COLUMN postcall_analysis_results.risk_level IS '风险等级字段，原始输出模式固定 unknown，不代表模型判断';
COMMENT ON COLUMN postcall_analysis_results.need_attention IS '是否需要关注字段，原始输出模式固定 false，不代表模型判断';
COMMENT ON COLUMN postcall_analysis_results.confidence IS '最终结论置信度，原始输出模式不写入';
COMMENT ON COLUMN postcall_analysis_results.risk_types IS '风险类型数组，原始输出模式保持空数组';
COMMENT ON COLUMN postcall_analysis_results.recommended_actions IS '建议动作数组，原始输出模式保持空数组';
COMMENT ON COLUMN postcall_analysis_results.fusion_trace IS '后续规则或融合过程追踪，原始输出模式保持空对象';
COMMENT ON COLUMN postcall_analysis_results.result_payload IS '完整 API 结果 JSON，原始输出模式下应与对外查询接口基本一致';
