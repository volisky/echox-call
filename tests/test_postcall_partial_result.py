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


if __name__ == "__main__":
    unittest.main()
