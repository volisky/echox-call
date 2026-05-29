import unittest

from echox_call.features.audio_analysis.postcall.llm_repository import (
    _build_summary,
    _select_overall_attention_level,
)


class LlmRepositorySummaryTests(unittest.TestCase):
    def test_summary_uses_analysis_summary_then_case_type_details(self) -> None:
        llm_out = {
            "caseTypeSummary": "分析总结：根据现有案件信息，明确命中二级以上警情，涉及人员死亡和举报涉枪涉爆线索，具体原因如下。",
            "caseTypeDetails": [
                {
                    "caseType": "人员死亡",
                    "reason": "报警内容中提到“有人死了”，语义指向人员死亡。不属于动物死亡等排除情形。",
                },
                {
                    "caseType": "举报涉枪涉爆线索",
                    "reason": "报警内容中提到“手枪”和“子弹”，具体敏感物品为手枪、子弹。需要进一步核查。",
                },
            ],
            "highRiskAddressSummary": "高发地址命中",
            "highRiskPersonSummary": "涉事人员存在风险标签",
        }

        summary = _build_summary(llm_out, "需要关注", "疑似哭泣")

        self.assertEqual(
            summary,
            [
                "分析总结：涉及人员死亡、举报涉枪涉爆线索。",
                "人员死亡：报警内容中提到“有人死了”，语义指向人员死亡。",
                "举报涉枪涉爆线索：报警内容中提到“手枪”和“子弹”，具体敏感物品为手枪、子弹。",
            ],
        )

    def test_summary_adds_analysis_prefix_when_missing(self) -> None:
        summary = _build_summary(
            {
                "caseTypeSummary": "现有信息未发现明确二级以上警情线索。",
                "caseTypeDetails": [
                    {
                        "caseType": "未命中二级以上警情",
                        "reason": "现有信息未出现明确二级及以上警情线索。",
                    }
                ],
            },
            "暂无明显线索",
            None,
        )

        self.assertEqual(
            summary,
            [
                "分析总结：未发现明确二级以上警情。",
                "未命中二级以上警情：现有信息未出现明确二级及以上警情线索。",
            ],
        )

    def test_summary_marks_review_suggested_as_suspected(self) -> None:
        summary = _build_summary(
            {
                "caseTypeDetails": [
                    {
                        "caseType": "校园欺凌",
                        "reason": "出现学生、被打、自残等信息，但持续性仍需核实。",
                    }
                ],
            },
            "建议复核",
            None,
        )

        self.assertEqual(
            summary,
            [
                "分析总结：疑似涉及校园欺凌，建议复核。",
                "校园欺凌：出现学生、被打、自残等信息，但持续性仍需核实。",
            ],
        )

    def test_summary_keeps_legacy_fallback(self) -> None:
        summary = _build_summary({}, "暂无明显线索", None)

        self.assertEqual(
            summary,
            [
                "分析总结：根据现有案件信息，综合判定为“暂无明显线索”。",
                "音频识别：暂无音频分析结果",
            ],
        )

    def test_summary_includes_secondary_review_confidence(self) -> None:
        summary = _build_summary(
            {
                "level": 2,
                "levelName": "建议复核",
                "secondaryReviewConfidence": 3,
                "secondaryReviewReason": "首次依据存在推断，需要人工复核。",
                "caseTypeDetails": [
                    {
                        "caseType": "人员死亡",
                        "reason": "仅提到倒地和抢救，未明确死亡。",
                    }
                ],
            },
            "建议复核",
            None,
        )

        self.assertEqual(
            summary,
            [
                "分析总结：疑似涉及人员死亡，建议复核。",
                "二次复核置信度：3/5，首次依据存在推断，需要人工复核。",
                "人员死亡：仅提到倒地和抢救，未明确死亡。",
            ],
        )

    def test_summary_mentions_audio_when_text_has_no_clue_but_audio_is_high_risk(self) -> None:
        summary = _build_summary(
            {
                "level": 3,
                "levelName": "暂无明显线索",
                "caseTypeDetails": [
                    {
                        "caseType": "未命中二级以上警情",
                        "reason": "现有信息未出现明确二级及以上警情线索。",
                    }
                ],
            },
            "需要关注",
            "需要关注",
            voice_level=1,
        )

        self.assertEqual(
            summary,
            [
                "分析总结：文本信息未发现明确二级以上警情，音频识别发现“需要关注”线索。",
                "未命中二级以上警情：现有信息未出现明确二级及以上警情线索。",
                "音频识别：综合判定为“需要关注”。",
            ],
        )

    def test_overall_attention_level_uses_highest_risk_between_llm_and_audio(self) -> None:
        self.assertEqual(_select_overall_attention_level(3, 1), (1, "需要关注"))
        self.assertEqual(_select_overall_attention_level(2, 1), (1, "需要关注"))
        self.assertEqual(_select_overall_attention_level(1, 2), (1, "需要关注"))
        self.assertEqual(_select_overall_attention_level(3, 2), (2, "建议复核"))
        self.assertEqual(_select_overall_attention_level(None, None), (3, "暂无明显线索"))


if __name__ == "__main__":
    unittest.main()
