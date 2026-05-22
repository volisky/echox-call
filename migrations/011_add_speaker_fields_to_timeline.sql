ALTER TABLE postcall_timeline_segments
    ADD COLUMN IF NOT EXISTS speaker_label text,
    ADD COLUMN IF NOT EXISTS speaker_role text,
    ADD COLUMN IF NOT EXISTS role_source text;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'postcall_timeline_speaker_label_not_blank'
    ) THEN
        ALTER TABLE postcall_timeline_segments
            ADD CONSTRAINT postcall_timeline_speaker_label_not_blank
            CHECK (speaker_label IS NULL OR btrim(speaker_label) <> '');
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'postcall_timeline_speaker_role_valid'
    ) THEN
        ALTER TABLE postcall_timeline_segments
            ADD CONSTRAINT postcall_timeline_speaker_role_valid
            CHECK (
                speaker_role IS NULL
                OR speaker_role IN ('未知', '报警人', '接警员')
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'postcall_timeline_role_source_valid'
    ) THEN
        ALTER TABLE postcall_timeline_segments
            ADD CONSTRAINT postcall_timeline_role_source_valid
            CHECK (
                role_source IS NULL
                OR role_source IN (
                    'global_audio',
                    'diarization_only',
                    'asr_timestamp',
                    'voiceprint',
                    'channel',
                    'manual'
                )
            );
    END IF;
END;
$$;

ALTER TABLE postcall_timeline_events
    DROP CONSTRAINT IF EXISTS postcall_timeline_event_type_valid;

ALTER TABLE postcall_timeline_events
    ADD CONSTRAINT postcall_timeline_event_type_valid CHECK (
        event_type IN (
            'audio_event_score',
            'voice_emotion_score',
            'voice_emotion_dimension',
            'voice_detailed_score',
            'speaker_diarization',
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
            'speaker_diarization',
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

COMMENT ON COLUMN postcall_timeline_segments.speaker_label IS '说话人分段标签，例如 SPEAKER_00；全局声音事件片段可为空';
COMMENT ON COLUMN postcall_timeline_segments.speaker_role IS '业务说话人角色：未知、报警人、接警员；当前无可靠映射依据时固定未知';
COMMENT ON COLUMN postcall_timeline_segments.role_source IS '说话人角色来源：global_audio 表示全局音频事件，diarization_only 表示仅完成说话人分段但未完成业务身份映射';
COMMENT ON COLUMN postcall_timeline_events.event_type IS '原始输出类型：audio_event_score、voice_emotion_score、voice_emotion_dimension、voice_detailed_score、speaker_diarization；其他类型为后续预留';
COMMENT ON COLUMN postcall_model_runs.model_role IS '模型或处理器职责：当前原始输出模式主要使用 audio_preprocess、speaker_diarization、audio_event、voice_emotion';
