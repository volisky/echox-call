COMMENT ON COLUMN postcall_timeline_segments.voice_emotion_scores IS 'WavLM 9 类情绪原始分数数组，对应 API timeline[].voiceEmotionScores；单项包含 index、emotionNameEn、emotionNameZh、score';
COMMENT ON COLUMN postcall_timeline_segments.voice_emotion_dimensions IS 'WavLM 连续维度原始输出，对应 API timeline[].voiceEmotionDimensions；每个维度包含 dimensionNameEn、dimensionNameZh、value';

COMMENT ON COLUMN postcall_timeline_events.event_code IS '原始输出编码，例如 AudioSet MID、WavLM emotion 英文标签、WavLM dimension key 或 17 类细粒度 index';
COMMENT ON COLUMN postcall_timeline_events.event_name IS '中文显示名，例如 尖叫声、恐惧、唤醒度；仅用于展示，不代表风险判断；17 类细粒度标签未确认时为空';
COMMENT ON COLUMN postcall_timeline_events.event_name_en IS '原始英文标签名，例如 Screaming、Fear、Arousal；17 类细粒度标签未确认时为空';
COMMENT ON COLUMN postcall_timeline_events.native_label IS '模型原生 index，例如 audioset_index_14、emotion_index_3、dimension_arousal、detailed_index_0';
