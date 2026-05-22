COMMENT ON COLUMN postcall_timeline_segments.audio_event_scores IS 'BEATs 对外声音事件分数数组，对应 API timeline[].audioEventScores；单项包含 eventNameEn、eventNameZh、score；index 和 MID 保存在 postcall_timeline_events 或 internal_payload';
COMMENT ON COLUMN postcall_timeline_segments.voice_emotion_scores IS 'WavLM 9 类情绪原始分数数组，对应 API timeline[].voiceEmotionScores；单项包含 emotionNameEn、emotionNameZh、score；emotion index 保存在 postcall_timeline_events 或 internal_payload';
COMMENT ON COLUMN postcall_analysis_results.result_payload IS '完整 API 结果 JSON；第一版对外不返回 analysis、audio、keySegments 等非模型输出字段';
COMMENT ON COLUMN postcall_analysis_results.audio_processing IS '音频处理摘要，例如总时长、切片数量、有效人声比例；内部排查字段，第一版默认不对外返回';
