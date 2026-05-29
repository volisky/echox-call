from __future__ import annotations

from datetime import datetime, timezone
import unittest

from echox_call.features.audio_analysis.postcall.repository import _build_partial_overall_result


class PostcallProcessingOverallResultTests(unittest.TestCase):
    def test_builds_llm_overall_result_before_audio_finishes(self) -> None:
        overall = _build_partial_overall_result(
            {
                "audio_completed_at": None,
                "audio_analysis_data": {},
                "llm_state": "completed",
                "llm_output": {
                    "level": 1,
                    "levelName": "需要关注",
                    "caseTypeDetails": [
                        {
                            "caseType": "举报涉枪涉爆线索",
                            "reason": "报警内容中提到“手枪”。",
                        }
                    ],
                },
                "raw_payload": {
                    "alarmContent": "报警人称看到手枪",
                    "alarmAddress": "某小区",
                    "isHighIncidentAddress": False,
                },
            }
        )

        self.assertIsNotNone(overall)
        assert overall is not None
        self.assertEqual(overall.level, 1)
        self.assertEqual(overall.summary[0], "分析总结：涉及举报涉枪涉爆线索。")
        self.assertIsNone(overall.voiceResult.level)
        self.assertEqual(overall.inputSnapshot.alarmAddress, "某小区")

    def test_builds_voice_overall_result_before_llm_finishes(self) -> None:
        overall = _build_partial_overall_result(
            {
                "audio_completed_at": datetime.now(timezone.utc),
                "audio_analysis_data": {
                    "attentionLevel": 2,
                    "attentionLevelName": "建议复核",
                    "reviewSegments": [
                        {
                            "startSec": 1.0,
                            "endSec": 3.5,
                            "result": "疑似喊叫线索",
                        }
                    ],
                },
                "llm_state": "processing",
                "llm_output": None,
                "raw_payload": {},
            }
        )

        self.assertIsNotNone(overall)
        assert overall is not None
        self.assertEqual(overall.level, 2)
        self.assertEqual(overall.levelName, "建议复核")
        self.assertEqual(overall.summary[0], "音频识别：综合判定为“建议复核”。")
        self.assertEqual(overall.voiceResult.reviewSegments[0].result, "疑似喊叫线索")

    def test_llm_partial_result_uses_highest_risk_between_llm_and_audio(self) -> None:
        overall = _build_partial_overall_result(
            {
                "audio_completed_at": datetime.now(timezone.utc),
                "audio_analysis_data": {
                    "attentionLevel": 1,
                    "attentionLevelName": "需要关注",
                    "reviewSegments": [
                        {
                            "startSec": 2.0,
                            "endSec": 4.0,
                            "result": "检测到高风险音频线索",
                        }
                    ],
                },
                "llm_state": "completed",
                "llm_output": {
                    "level": 3,
                    "levelName": "暂无明显线索",
                    "caseTypeDetails": [
                        {
                            "caseType": "未命中二级以上警情",
                            "reason": "现有信息未出现明确二级及以上警情线索。",
                        }
                    ],
                },
                "raw_payload": {},
            }
        )

        self.assertIsNotNone(overall)
        assert overall is not None
        self.assertEqual(overall.level, 1)
        self.assertEqual(overall.levelName, "需要关注")
        self.assertEqual(
            overall.summary[0],
            "分析总结：文本信息未发现明确二级以上警情，音频识别发现“需要关注”线索。",
        )
        self.assertEqual(overall.voiceResult.level, 1)


if __name__ == "__main__":
    unittest.main()
