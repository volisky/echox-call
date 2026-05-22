DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'postcall_jobs'
          AND column_name = 'status'
    )
    AND NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'postcall_jobs'
          AND column_name = 'state'
    ) THEN
        EXECUTE 'ALTER TABLE postcall_jobs RENAME COLUMN status TO state';
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'postcall_jobs_status_valid'
    )
    AND NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'postcall_jobs_state_valid'
    ) THEN
        EXECUTE 'ALTER TABLE postcall_jobs RENAME CONSTRAINT postcall_jobs_status_valid TO postcall_jobs_state_valid';
    END IF;
END $$;

ALTER INDEX IF EXISTS idx_postcall_jobs_status_created_at
    RENAME TO idx_postcall_jobs_state_created_at;

COMMENT ON COLUMN postcall_jobs.state IS '任务状态：processing_queued 等待处理，processing_downloading 正在下载音频，processing_analyzing 正在模型分析，completed 已完成，failed 执行失败，failed_cancelled 已取消';
