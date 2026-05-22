ALTER TABLE postcall_analysis_results
    ADD COLUMN IF NOT EXISTS analysis_mode text NOT NULL DEFAULT 'raw_model_outputs',
    ADD COLUMN IF NOT EXISTS risk_evaluated boolean NOT NULL DEFAULT false;

ALTER TABLE postcall_analysis_results
    ALTER COLUMN confidence TYPE double precision
    USING confidence::double precision;

ALTER TABLE postcall_timeline_events
    ALTER COLUMN score TYPE double precision
    USING score::double precision;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'postcall_analysis_results_mode_valid'
    ) THEN
        ALTER TABLE postcall_analysis_results
            ADD CONSTRAINT postcall_analysis_results_mode_valid
            CHECK (
                analysis_mode IN (
                    'raw_model_outputs',
                    'rule_evaluated',
                    'model_fusion'
                )
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'postcall_analysis_results_mode_risk_consistent'
    ) THEN
        ALTER TABLE postcall_analysis_results
            ADD CONSTRAINT postcall_analysis_results_mode_risk_consistent
            CHECK (
                (
                    analysis_mode = 'raw_model_outputs'
                    AND risk_evaluated = false
                    AND risk_level = 'unknown'
                    AND need_attention = false
                    AND confidence IS NULL
                    AND cardinality(risk_types) = 0
                    AND cardinality(recommended_actions) = 0
                    AND fusion_trace = '{}'::jsonb
                )
                OR (
                    analysis_mode <> 'raw_model_outputs'
                    AND risk_evaluated = true
                )
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'postcall_timeline_events_raw_fields_neutral'
    ) THEN
        ALTER TABLE postcall_timeline_events
            ADD CONSTRAINT postcall_timeline_events_raw_fields_neutral
            CHECK (
                event_type NOT IN (
                    'audio_event_score',
                    'voice_emotion_score',
                    'voice_emotion_dimension',
                    'voice_detailed_score'
                )
                OR (
                    confidence_level IS NULL
                    AND severity IS NULL
                )
            );
    END IF;
END;
$$;

COMMENT ON COLUMN postcall_analysis_results.analysis_mode IS '分析模式：raw_model_outputs 表示仅保存模型原始输出；rule_evaluated 和 model_fusion 预留给后续规则或融合阶段';
COMMENT ON COLUMN postcall_analysis_results.risk_evaluated IS '是否已经做风险判断；原始输出模式固定为 false';
COMMENT ON COLUMN postcall_analysis_results.confidence IS '最终结论置信度，原始输出模式不写入；使用 double precision 避免模型分数精度损失';
COMMENT ON COLUMN postcall_timeline_events.score IS '模型原始分数或归一化概率，范围 0 到 1；使用 double precision 保留模型输出精度';
