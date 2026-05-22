COMMENT ON COLUMN postcall_timeline_segments.audio_event_scores IS 'BEATs 原始声音事件分数数组，对应 API timeline[].audioEventScores；单项包含 index、mid、eventNameEn、eventNameZh、score';
COMMENT ON COLUMN postcall_timeline_segments.voice_emotion_scores IS 'WavLM 9 类情绪原始分数数组，对应 API timeline[].voiceEmotionScores；单项包含 index、label、score';
COMMENT ON COLUMN postcall_timeline_segments.voice_detailed_scores IS 'WavLM 17 类细粒度原始分数数组，对应 API timeline[].voiceDetailedScores；标签未确认时只保存 index 和 score';
COMMENT ON COLUMN postcall_timeline_segments.voice_emotion_dimensions IS 'WavLM 连续维度原始输出，对应 API timeline[].voiceEmotionDimensions，包含 arousal、valence、dominance';

COMMENT ON COLUMN postcall_timeline_events.event_name IS '中文显示名，例如 尖叫声、恐惧、唤醒度；仅用于展示，不代表风险判断；无中文显示名时可为空或使用英文名';
COMMENT ON COLUMN postcall_timeline_events.event_name_en IS '原始英文标签名，例如 Screaming、Fear、arousal';
COMMENT ON COLUMN postcall_timeline_events.native_label IS '模型原生 index，例如 audioset_index_14、emotion_index_3、detailed_index_0';
