from __future__ import annotations

from types import SimpleNamespace
import unittest

from echox_call.features.audio_analysis.postcall.llm_worker import (
    _SYSTEM_PROMPT,
    _build_user_message,
    _extract_analysis_output,
    _normalize_case_type_details,
)


class LlmWorkerOutputTest(unittest.TestCase):
    def test_json_only_prompt_does_not_request_tool_calling(self) -> None:
        job = SimpleNamespace(
            asr_result=[],
            alarm_content=None,
            alarm_address=None,
            is_high_incident_address=None,
            risk_person=None,
        )

        message = _build_user_message(job, use_tools=False)

        self.assertIn("请只输出一个 JSON 对象", message)
        self.assertIn("caseTypeDetails", message)
        self.assertNotIn("submit_analysis", message)

    def test_system_prompt_prevents_injury_or_fighting_death_inference(self) -> None:
        self.assertIn("不得根据常识、风险、经验进行联想、延申或补全", _SYSTEM_PROMPT)
        self.assertIn("受伤", _SYSTEM_PROMPT)
        self.assertIn("打架", _SYSTEM_PROMPT)
        self.assertIn("不能推断为“人员死亡”", _SYSTEM_PROMPT)
        self.assertIn("不得在原因中写“可能死亡”", _SYSTEM_PROMPT)

    def test_extracts_submit_analysis_tool_arguments(self) -> None:
        choice = SimpleNamespace(
            message=SimpleNamespace(
                tool_calls=[
                    SimpleNamespace(
                        function=SimpleNamespace(
                            name="submit_analysis",
                            arguments='{"level": 2, "levelName": "建议复核"}',
                        )
                    )
                ],
                content=None,
            )
        )

        self.assertEqual(
            _extract_analysis_output(choice),
            {"level": 2, "levelName": "建议复核"},
        )

    def test_extracts_plain_json_content(self) -> None:
        choice = SimpleNamespace(
            message=SimpleNamespace(
                tool_calls=None,
                content=(
                    "```json\n"
                    '{"level": 3, "levelName": "暂无明显线索", '
                    '"caseTypeSummary": "分析总结：未命中", '
                    '"caseTypeDetails": [{"caseType": "未命中二级以上警情", '
                    '"reason": "现有信息未出现明确二级及以上警情线索。"}]}\n'
                    "```"
                ),
            )
        )

        self.assertEqual(
            _extract_analysis_output(choice),
            {
                "level": 3,
                "levelName": "暂无明显线索",
                "caseTypeSummary": "分析总结：未命中",
                "caseTypeDetails": [
                    {
                        "caseType": "未命中二级以上警情",
                        "reason": "现有信息未出现明确二级及以上警情线索。",
                    }
                ],
            },
        )

    def test_extracts_json_object_from_explanatory_content(self) -> None:
        choice = SimpleNamespace(
            message=SimpleNamespace(
                tool_calls=None,
                content=(
                    "分析结果如下：\n"
                    '{"level": 1, "levelName": "需要关注", '
                    '"highRiskPersonSummary": "存在风险人员信息"}\n'
                    "请以系统结果为准。"
                ),
            )
        )

        self.assertEqual(
            _extract_analysis_output(choice),
            {
                "level": 1,
                "levelName": "需要关注",
                "highRiskPersonSummary": "存在风险人员信息",
            },
        )

    def test_normalizes_case_type_details(self) -> None:
        self.assertEqual(
            _normalize_case_type_details(
                [
                    {
                        "caseType": "举报涉枪涉爆线索",
                        "reason": "报警内容中提到“手枪”和“子弹”。",
                    },
                    {"caseType": "", "reason": "无类型"},
                    "bad",
                ]
            ),
            [
                {
                    "caseType": "举报涉枪涉爆线索",
                    "reason": "报警内容中提到“手枪”和“子弹”。",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
