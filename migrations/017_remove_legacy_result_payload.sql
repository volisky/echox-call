ALTER TABLE postcall_analysis_results
    DROP CONSTRAINT IF EXISTS postcall_analysis_results_json_objects;

ALTER TABLE postcall_analysis_results
    DROP COLUMN IF EXISTS result_payload;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'postcall_analysis_results_json_objects'
    ) THEN
        ALTER TABLE postcall_analysis_results
            ADD CONSTRAINT postcall_analysis_results_json_objects
            CHECK (
                jsonb_typeof(model_versions) = 'object'
                AND jsonb_typeof(audio_processing) = 'object'
                AND jsonb_typeof(fusion_trace) = 'object'
                AND jsonb_typeof(api_result_payload) = 'object'
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'postcall_analysis_results_api_snapshot_for_rule'
    ) THEN
        ALTER TABLE postcall_analysis_results
            ADD CONSTRAINT postcall_analysis_results_api_snapshot_for_rule
            CHECK (
                analysis_mode <> 'rule_evaluated'
                OR (
                    api_result_generated_at IS NOT NULL
                    AND api_result_payload <> '{}'::jsonb
                    AND api_result_payload ?& ARRAY[
                        'jobId',
                        'jjdh',
                        'state',
                        'needAttention',
                        'timeline',
                        'insights'
                    ]
                )
            );
    END IF;
END;
$$;

COMMENT ON COLUMN postcall_analysis_results.api_result_payload IS '对外 GET /api/v1/postcall/jobs/{jobId} 返回的 data 部分快照，是当前 API 结果的唯一 JSON 快照；不包含 code、message、timestamp';

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
            'fusion'
        )
    );

COMMENT ON COLUMN postcall_timeline_events.event_type IS '事件类型：audio_event_score、voice_emotion_score、voice_emotion_dimension、voice_detailed_score、speaker_diarization、derived_risk、audio_quality、system';
COMMENT ON COLUMN postcall_model_runs.model_role IS '模型或处理器职责：audio_preprocess、speaker_diarization、vad、audio_event、voice_emotion、voice_emotion_detail、embedding、reranker、fusion';
