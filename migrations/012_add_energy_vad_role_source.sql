ALTER TABLE postcall_timeline_segments
    DROP CONSTRAINT IF EXISTS postcall_timeline_role_source_valid;

ALTER TABLE postcall_timeline_segments
    ADD CONSTRAINT postcall_timeline_role_source_valid CHECK (
        role_source IS NULL
        OR role_source IN (
            'global_audio',
            'diarization_only',
            'energy_vad',
            'asr_timestamp',
            'voiceprint',
            'channel',
            'manual'
        )
    );

COMMENT ON COLUMN postcall_timeline_segments.role_source IS '说话人或切片来源：global_audio 表示全局音频事件，diarization_only 表示 pyannote 仅完成说话人分段但未完成业务身份映射，energy_vad 表示快模式下仅按能量人声切片且不区分说话人';
