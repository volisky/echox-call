ALTER TABLE postcall_jobs
    DROP CONSTRAINT IF EXISTS postcall_jobs_status_valid;

ALTER TABLE postcall_jobs
    ALTER COLUMN status SET DEFAULT 'processing_queued';

UPDATE postcall_jobs
SET status = CASE status
    WHEN 'queued' THEN 'processing_queued'
    WHEN 'downloading' THEN 'processing_downloading'
    WHEN 'analyzing' THEN 'processing_analyzing'
    WHEN 'completed' THEN 'completed'
    WHEN 'failed' THEN 'failed'
    WHEN 'cancelled' THEN 'failed_cancelled'
    ELSE status
END
WHERE status IN ('queued', 'downloading', 'analyzing', 'completed', 'failed', 'cancelled');

ALTER TABLE postcall_jobs
    ADD CONSTRAINT postcall_jobs_status_valid CHECK (
        status IN (
            'processing_queued',
            'processing_downloading',
            'processing_analyzing',
            'completed',
            'failed',
            'failed_cancelled'
        )
    );

COMMENT ON COLUMN postcall_jobs.status IS '任务状态：processing_queued 等待处理，processing_downloading 正在下载音频，processing_analyzing 正在模型分析，completed 已完成，failed 执行失败，failed_cancelled 已取消';
