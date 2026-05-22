-- Tighten the postcall result contract so database columns and API snapshots
-- keep the same precision and shape. This migration intentionally removes
-- tolerance for "basically similar" timeline payloads.

ALTER TABLE postcall_timeline_segments
    ALTER COLUMN start_sec TYPE double precision USING start_sec::double precision,
    ALTER COLUMN end_sec TYPE double precision USING end_sec::double precision;

ALTER TABLE postcall_timeline_events
    ALTER COLUMN start_sec TYPE double precision USING start_sec::double precision,
    ALTER COLUMN end_sec TYPE double precision USING end_sec::double precision;

ALTER TABLE postcall_evidence_segments
    ALTER COLUMN start_sec TYPE double precision USING start_sec::double precision,
    ALTER COLUMN end_sec TYPE double precision USING end_sec::double precision;

ALTER TABLE postcall_audio_assets
    ALTER COLUMN duration_sec TYPE double precision USING duration_sec::double precision;

UPDATE postcall_timeline_segments
SET
    start_sec = (segment_payload->>'startSec')::double precision,
    end_sec = (segment_payload->>'endSec')::double precision
WHERE segment_payload ?& ARRAY['startSec', 'endSec']
  AND jsonb_typeof(segment_payload->'startSec') = 'number'
  AND jsonb_typeof(segment_payload->'endSec') = 'number';

UPDATE postcall_timeline_events AS event
SET
    start_sec = segment.start_sec,
    end_sec = segment.end_sec,
    segment_id = segment.segment_id
FROM postcall_timeline_segments AS segment
WHERE event.timeline_segment_id = segment.id;

UPDATE postcall_model_runs
SET model_version = CASE
    WHEN model_role = 'audio_preprocess' THEN 'audio_processing_v1'
    WHEN model_name = 'BEATs_iter3_plus_AS2M_finetuned_on_AS2M_cpt2'
        THEN 'BEATs_iter3_plus_AS2M_finetuned_on_AS2M_cpt2.pt'
    WHEN model_name = 'wavlm-large-categorical-emotion'
        THEN 'wavlm-large-categorical-emotion'
    WHEN model_name = 'pyannote-speaker-diarization-community-1'
        THEN 'speaker-diarization-community-1'
    ELSE model_version
END
WHERE model_version IS NULL;

ALTER TABLE postcall_model_runs
    DROP CONSTRAINT IF EXISTS postcall_model_runs_version_not_blank;

ALTER TABLE postcall_model_runs
    ADD CONSTRAINT postcall_model_runs_version_not_blank
    CHECK (model_version IS NULL OR btrim(model_version) <> '');

ALTER TABLE postcall_analysis_results
    DROP CONSTRAINT IF EXISTS postcall_analysis_results_api_snapshot_for_rule;

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
                'needAttention',
                'timeline',
                'insights'
            ]
            AND jsonb_typeof(api_result_payload->'jobId') = 'string'
            AND jsonb_typeof(api_result_payload->'jjdh') = 'string'
            AND jsonb_typeof(api_result_payload->'state') = 'string'
            AND api_result_payload->>'state' = 'completed'
            AND jsonb_typeof(api_result_payload->'needAttention') = 'boolean'
            AND jsonb_typeof(api_result_payload->'timeline') = 'array'
            AND jsonb_typeof(api_result_payload->'insights') = 'array'
            AND NOT (
                api_result_payload ?| ARRAY[
                    'analysis',
                    'audio',
                    'riskLevel',
                    'analysisMode',
                    'riskEvaluated',
                    'beats',
                    'wavlm',
                    'keySegments',
                    'confidenceLevel'
                ]
            )
        )
    );

ALTER TABLE postcall_analysis_results
    DROP CONSTRAINT IF EXISTS postcall_analysis_results_rule_v1_neutral;

ALTER TABLE postcall_analysis_results
    ADD CONSTRAINT postcall_analysis_results_rule_v1_neutral
    CHECK (
        analysis_mode <> 'rule_evaluated'
        OR (
            risk_evaluated = true
            AND risk_level = 'unknown'
            AND confidence IS NULL
            AND cardinality(risk_types) = 0
            AND cardinality(recommended_actions) = 0
        )
    );

ALTER TABLE postcall_timeline_segments
    DROP CONSTRAINT IF EXISTS postcall_timeline_segments_payload_contract;

ALTER TABLE postcall_timeline_segments
    ADD CONSTRAINT postcall_timeline_segments_payload_contract
    CHECK (
        segment_payload ?& ARRAY[
            'segmentId',
            'startSec',
            'endSec',
            'speakerLabel',
            'speakerRole',
            'roleSource',
            'audioEventScores',
            'voiceEmotionScores',
            'voiceEmotionDimensions'
        ]
        AND jsonb_typeof(segment_payload->'segmentId') = 'string'
        AND segment_payload->>'segmentId' = segment_id
        AND jsonb_typeof(segment_payload->'startSec') = 'number'
        AND abs(((segment_payload->>'startSec')::double precision) - start_sec) < 0.000000001
        AND jsonb_typeof(segment_payload->'endSec') = 'number'
        AND abs(((segment_payload->>'endSec')::double precision) - end_sec) < 0.000000001
        AND jsonb_typeof(segment_payload->'audioEventScores') = 'array'
        AND segment_payload->'audioEventScores' = audio_event_scores
        AND jsonb_typeof(segment_payload->'voiceEmotionScores') = 'array'
        AND segment_payload->'voiceEmotionScores' = voice_emotion_scores
        AND jsonb_typeof(segment_payload->'voiceEmotionDimensions') = 'object'
        AND segment_payload->'voiceEmotionDimensions' = voice_emotion_dimensions
        AND (
            (speaker_label IS NULL AND jsonb_typeof(segment_payload->'speakerLabel') = 'null')
            OR (speaker_label IS NOT NULL AND segment_payload->>'speakerLabel' = speaker_label)
        )
        AND (
            (speaker_role IS NULL AND jsonb_typeof(segment_payload->'speakerRole') = 'null')
            OR (speaker_role IS NOT NULL AND segment_payload->>'speakerRole' = speaker_role)
        )
        AND (
            (role_source IS NULL AND jsonb_typeof(segment_payload->'roleSource') = 'null')
            OR (role_source IS NOT NULL AND segment_payload->>'roleSource' = role_source)
        )
        AND NOT (
            segment_payload ?| ARRAY[
                'voiceDetailedScores',
                'beats',
                'wavlm',
                'index',
                'mid'
            ]
        )
    );

ALTER TABLE postcall_timeline_segments
    DROP CONSTRAINT IF EXISTS postcall_timeline_segments_source_contract;

ALTER TABLE postcall_timeline_segments
    ADD CONSTRAINT postcall_timeline_segments_source_contract
    CHECK (
        (role_source <> 'global_audio' OR (speaker_label IS NULL AND speaker_role IS NULL))
        AND (role_source <> 'energy_vad' OR (speaker_label IS NULL AND speaker_role IS NULL))
        AND (
            role_source <> 'diarization_only'
            OR (speaker_label IS NOT NULL AND speaker_role = '未知')
        )
    );

ALTER TABLE postcall_timeline_segments
    DROP CONSTRAINT IF EXISTS postcall_timeline_segments_model_output_contract;

ALTER TABLE postcall_timeline_segments
    ADD CONSTRAINT postcall_timeline_segments_model_output_contract
    CHECK (
        (
            role_source <> 'global_audio'
            OR (
                jsonb_array_length(audio_event_scores) > 0
                AND jsonb_array_length(voice_emotion_scores) = 0
                AND voice_emotion_dimensions = '{}'::jsonb
            )
        )
        AND (
            role_source NOT IN ('diarization_only', 'energy_vad')
            OR (
                jsonb_array_length(audio_event_scores) = 0
                AND jsonb_array_length(voice_emotion_scores) = 9
                AND voice_emotion_dimensions ?& ARRAY['arousal', 'valence', 'dominance']
            )
        )
    );

COMMENT ON COLUMN postcall_timeline_segments.start_sec IS '时间线片段开始时间，单位秒，对应 API timeline[].startSec；使用 double precision 保持模型窗口原始精度';
COMMENT ON COLUMN postcall_timeline_segments.end_sec IS '时间线片段结束时间，单位秒，对应 API timeline[].endSec；使用 double precision 保持模型窗口原始精度';
COMMENT ON COLUMN postcall_timeline_segments.segment_payload IS '与 API timeline[] 单个片段完全一致的对外 JSON；数据库约束要求它与结构化列保持一致，不包含内部 index、mid、voiceDetailedScores';
COMMENT ON COLUMN postcall_timeline_events.start_sec IS '事件开始时间，单位秒；使用 double precision 与所属 timeline 片段保持一致';
COMMENT ON COLUMN postcall_timeline_events.end_sec IS '事件结束时间，单位秒；使用 double precision 与所属 timeline 片段保持一致';
COMMENT ON COLUMN postcall_evidence_segments.start_sec IS '证据片段开始时间，单位秒；使用 double precision 保存规则线索时间';
COMMENT ON COLUMN postcall_evidence_segments.end_sec IS '证据片段结束时间，单位秒；使用 double precision 保存规则线索时间';
COMMENT ON COLUMN postcall_audio_assets.duration_sec IS '音频时长，单位秒；使用 double precision 保存音频实际时长';
COMMENT ON COLUMN postcall_analysis_results.api_result_payload IS '对外 GET /api/v1/postcall/jobs/{jobId} 返回的 data 部分快照，是当前 API 结果的唯一 JSON 快照；不包含 code、message、timestamp；规则线索结果必须包含 jobId、jjdh、state、needAttention、timeline、insights';
COMMENT ON COLUMN postcall_model_runs.model_version IS '模型版本、checkpoint 名称或处理器版本号；worker 成功记录应写入可追溯版本';
