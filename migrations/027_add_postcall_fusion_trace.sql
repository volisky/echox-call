-- 027_add_postcall_fusion_trace.sql
-- 重新增加规则线索层内部追踪字段。外部 API 仍只返回 level、levelName、reviewSegments。

ALTER TABLE postcall_analysis_results
    ADD COLUMN IF NOT EXISTS fusion_trace jsonb NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE postcall_analysis_results
    DROP CONSTRAINT IF EXISTS postcall_analysis_results_fusion_trace_object;

ALTER TABLE postcall_analysis_results
    ADD CONSTRAINT postcall_analysis_results_fusion_trace_object
    CHECK (jsonb_typeof(fusion_trace) = 'object');

COMMENT ON COLUMN postcall_analysis_results.fusion_trace IS
    '规则线索层内部完整追踪 JSON：保存三等级结论、命中复合线索、被压制线索、模型冲突、不确定性和调试信息；不直接作为外部 API 返回。';
