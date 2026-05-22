-- Remove callback delivery persistence from the current postcall schema.
-- callbackUrl remains a raw task field on postcall_jobs, but no delivery
-- queue or retry table is maintained in this version.

DROP TABLE IF EXISTS postcall_callback_deliveries;

COMMENT ON COLUMN postcall_jobs.callback_url IS '上游回调地址，可为空；当前仅作为任务原始请求字段保存，不创建回调投递记录';
