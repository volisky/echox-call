COMMENT ON COLUMN postcall_analysis_results.analysis_mode IS '分析模式：raw_model_outputs 表示仅保存模型原始输出；rule_evaluated 表示已执行规则线索层；model_fusion 为后续模型融合预留';
COMMENT ON COLUMN postcall_analysis_results.risk_evaluated IS '是否已经执行规则或融合判断；rule_evaluated 结果固定为 true';
COMMENT ON COLUMN postcall_analysis_results.need_attention IS '规则线索层输出的是否需要关注；第一版只表示需要关注/不需要关注，不代表风险等级';
COMMENT ON COLUMN postcall_analysis_results.fusion_trace IS '规则或融合过程追踪信息；规则线索层保存 ruleVersion 和 matchedRuleCodes';
COMMENT ON COLUMN postcall_analysis_results.result_payload IS '对外 API 白名单结果快照，包含 jobId、jjdh、state、needAttention、timeline、insights';

COMMENT ON TABLE postcall_evidence_segments IS '报警音频规则证据片段表，用于保存 needAttention=true 的线索，便于人工重点回听和解释';
COMMENT ON COLUMN postcall_evidence_segments.segment_id IS '证据片段编号，规则线索层使用 insight_000001 这类中性编号，同一任务内唯一';
COMMENT ON COLUMN postcall_evidence_segments.risk_level IS '该证据片段对应的风险等级；第一版规则线索层固定为 unknown';
COMMENT ON COLUMN postcall_evidence_segments.reason IS '该片段被选为需要关注证据的原因，来自规则模板';
COMMENT ON COLUMN postcall_evidence_segments.payload IS '证据片段详情 JSON，保存完整 insight、matchedRuleCodes、evidence 和 ruleVersion';
