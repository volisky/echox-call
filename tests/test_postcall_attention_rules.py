from __future__ import annotations

from pathlib import Path
import unittest

from pydantic import ValidationError
import yaml

from echox_call.features.audio_analysis.postcall.attention_rules import (
    AttentionRulesEngine,
    load_attention_rules,
)
from echox_call.features.audio_analysis.postcall.schemas import PostcallJobResultData


RULES_PATH = Path(__file__).resolve().parents[1] / "config" / "postcall_attention_rules.yaml"


def _event_segment(
    segment_id: str,
    start_sec: float,
    end_sec: float,
    event_name_en: str,
    score: float,
    *,
    event_name_zh: str | None = None,
) -> dict[str, object]:
    return {
        "segmentId": segment_id,
        "startSec": start_sec,
        "endSec": end_sec,
        "speakerLabel": None,
        "speakerRole": None,
        "roleSource": "global_audio",
        "audioEventScores": [
            {
                "eventNameEn": event_name_en,
                "eventNameZh": event_name_zh or event_name_en,
                "score": score,
            }
        ],
        "voiceEmotionScores": [],
        "voiceEmotionDimensions": {},
    }


def _voice_segment(
    segment_id: str,
    start_sec: float,
    end_sec: float,
    *,
    emotion_name_en: str | None = None,
    emotion_score: float | None = None,
    arousal: float = 0.5,
    valence: float = 0.5,
    dominance: float = 0.5,
    speaker_label: str = "SPEAKER_00",
) -> dict[str, object]:
    emotion_scores = []
    if emotion_name_en is not None and emotion_score is not None:
        emotion_scores.append(
            {
                "emotionNameEn": emotion_name_en,
                "emotionNameZh": emotion_name_en,
                "score": emotion_score,
            }
        )
    return {
        "segmentId": segment_id,
        "startSec": start_sec,
        "endSec": end_sec,
        "speakerLabel": speaker_label,
        "speakerRole": "未知",
        "roleSource": "diarization_only",
        "audioEventScores": [],
        "voiceEmotionScores": emotion_scores,
        "voiceEmotionDimensions": {
            "arousal": {
                "dimensionNameEn": "Arousal",
                "dimensionNameZh": "唤醒度",
                "value": arousal,
            },
            "valence": {
                "dimensionNameEn": "Valence",
                "dimensionNameZh": "情绪效价",
                "value": valence,
            },
            "dominance": {
                "dimensionNameEn": "Dominance",
                "dimensionNameZh": "控制感",
                "value": dominance,
            },
        },
    }


class PostcallAttentionRulesV6Test(unittest.TestCase):
    def setUp(self) -> None:
        self.rules = load_attention_rules(RULES_PATH)

    def _evaluate(self, timeline: list[dict[str, object]]):
        return self.rules.evaluate(timeline)

    def test_01_high_confidence_screaming_needs_attention(self) -> None:
        result = self._evaluate([_event_segment("seg_1", 0, 10, "Screaming", 0.8)])

        self.assertEqual(result.level, 1)
        self.assertEqual(result.attention_conclusion, "need_attention")
        self.assertIn("screaming_high_confidence", result.matched_rule_codes)
        self.assertEqual(result.review_segments[0]["title"], "疑似尖叫线索")

    def test_02_screaming_with_impact_needs_attention(self) -> None:
        result = self._evaluate(
            [
                _event_segment("seg_1", 0, 5, "Screaming", 0.64),
                _event_segment("seg_2", 4, 9, "Bang", 0.56),
            ]
        )

        self.assertEqual(result.level, 1)
        self.assertIn("screaming_with_impact", result.matched_rule_codes)
        self.assertEqual(result.review_segments[0]["title"], "疑似尖叫伴随冲击线索")

    def test_03_screaming_with_fear_needs_attention(self) -> None:
        result = self._evaluate(
            [
                _event_segment("seg_1", 0, 5, "Screaming", 0.62),
                _voice_segment("seg_2", 3, 8, emotion_name_en="Fear", emotion_score=0.6),
            ]
        )

        self.assertEqual(result.level, 1)
        self.assertIn("screaming_with_fear_or_breathing", result.matched_rule_codes)

    def test_04_infant_crying_with_adult_shouting_needs_attention(self) -> None:
        result = self._evaluate(
            [
                _event_segment("seg_1", 0, 8, "Baby cry, infant cry", 0.7),
                _event_segment("seg_2", 5, 10, "Shout", 0.62),
            ]
        )

        self.assertEqual(result.level, 1)
        self.assertIn("infant_crying_with_conflict", result.matched_rule_codes)

    def test_05_infant_crying_with_breaking_needs_attention(self) -> None:
        result = self._evaluate(
            [
                _event_segment("seg_1", 0, 8, "Baby cry, infant cry", 0.68),
                _event_segment("seg_2", 7, 12, "Glass", 0.5),
            ]
        )

        self.assertEqual(result.level, 1)
        self.assertIn("infant_crying_with_conflict", result.matched_rule_codes)

    def test_06_sustained_adult_crying_needs_attention(self) -> None:
        result = self._evaluate([_event_segment("seg_1", 0, 10, "Crying, sobbing", 0.56)])

        self.assertEqual(result.level, 1)
        self.assertIn("adult_crying_sustained_or_repeated", result.matched_rule_codes)

    def test_07_single_low_confidence_crying_is_suppressed(self) -> None:
        result = self._evaluate([_event_segment("seg_1", 0, 4, "Crying, sobbing", 0.52)])

        self.assertEqual(result.level, 3)
        self.assertFalse(result.review_segments)
        self.assertTrue(result.suppressed_insights)

    def test_08_abnormal_breathing_with_fear_needs_attention(self) -> None:
        result = self._evaluate(
            [
                _event_segment("seg_1", 0, 6, "Gasp", 0.56),
                _voice_segment("seg_2", 5, 9, emotion_name_en="Fear", emotion_score=0.58),
            ]
        )

        self.assertEqual(result.level, 1)
        self.assertIn("breathing_distress_with_voice", result.matched_rule_codes)

    def test_09_abnormal_breathing_thresholds(self) -> None:
        low = self._evaluate([_event_segment("seg_1", 0, 5, "Pant", 0.52)])
        medium = self._evaluate([_event_segment("seg_1", 0, 5, "Pant", 0.56)])

        self.assertEqual(low.level, 3)
        self.assertEqual(medium.level, 2)
        self.assertIn("abnormal_breathing_medium_review", medium.matched_rule_codes)

    def test_10_repeated_impact_suggests_review(self) -> None:
        result = self._evaluate(
            [
                _event_segment("seg_1", 0, 5, "Bang", 0.56),
                _event_segment("seg_2", 7, 12, "Thump, thud", 0.57),
            ]
        )

        self.assertEqual(result.level, 2)
        self.assertIn("repeated_blunt_impact_review", result.matched_rule_codes)

    def test_11_repeated_impact_with_tension_needs_attention(self) -> None:
        result = self._evaluate(
            [
                _event_segment("seg_1", 0, 5, "Bang", 0.56),
                _event_segment("seg_2", 7, 12, "Thump, thud", 0.57),
                _voice_segment("seg_3", 9, 13, arousal=0.76),
            ]
        )

        self.assertEqual(result.level, 1)
        self.assertIn("conflict_multi_signal_near", result.matched_rule_codes)

    def test_12_single_low_impact_is_suppressed(self) -> None:
        result = self._evaluate([_event_segment("seg_1", 0, 4, "Bang", 0.52)])

        self.assertEqual(result.level, 3)
        self.assertFalse(result.review_segments)

    def test_13_plain_knocking_is_suppressed(self) -> None:
        result = self._evaluate([_event_segment("seg_1", 0, 5, "Knock", 0.65)])

        self.assertEqual(result.level, 3)
        self.assertFalse(result.review_segments)

    def test_14_knocking_with_fear_suggests_review(self) -> None:
        result = self._evaluate(
            [
                _event_segment("seg_1", 0, 5, "Knock", 0.62),
                _voice_segment("seg_2", 4, 7, emotion_name_en="Fear", emotion_score=0.59),
            ]
        )

        self.assertEqual(result.level, 2)
        self.assertIn("knocking_with_distress_context", result.matched_rule_codes)

    def test_15_breaking_with_high_arousal_needs_attention(self) -> None:
        result = self._evaluate(
            [
                _event_segment("seg_1", 0, 5, "Glass", 0.5),
                _voice_segment("seg_2", 3, 8, arousal=0.76),
            ]
        )

        self.assertEqual(result.level, 1)
        self.assertIn("breaking_damage_with_voice_or_door", result.matched_rule_codes)

    def test_16_background_event_high_voice_calm_suggests_review(self) -> None:
        result = self._evaluate(
            [
                _event_segment("seg_1", 0, 5, "Shout", 0.66),
                _voice_segment("seg_2", 3, 8, emotion_name_en="Neutral", emotion_score=0.62),
            ]
        )

        self.assertEqual(result.level, 2)
        self.assertEqual(result.conflict_status, "audio_event_high_voice_neutral")

    def test_17_voice_fear_without_background_event_stays_internal(self) -> None:
        result = self._evaluate(
            [
                _voice_segment(
                    "seg_1",
                    0,
                    4,
                    emotion_name_en="Fear",
                    emotion_score=0.78,
                    arousal=0.8,
                )
            ]
        )

        self.assertEqual(result.level, 3)
        self.assertFalse(result.review_segments)
        self.assertNotIn("voice_abnormal_without_audio_event", result.matched_rule_codes)
        self.assertIn("voice_distress_without_background_event", result.matched_composite_insights)

    def test_voice_dimensions_only_do_not_suggest_review(self) -> None:
        result = self._evaluate(
            [
                _voice_segment("seg_1", 3, 28, arousal=0.72, valence=0.333, dominance=0.689),
                _voice_segment("seg_2", 31, 57, arousal=0.73, valence=0.334, dominance=0.673),
            ]
        )

        self.assertEqual(result.level, 3)
        self.assertFalse(result.review_segments)
        self.assertEqual(result.matched_rule_codes, [])
        self.assertIn("voice_emotional_stress", result.matched_composite_insights)

    def test_18_multiple_speakers_uncertainty_stays_internal(self) -> None:
        result = self._evaluate(
            [
                _voice_segment("seg_1", 0, 2, speaker_label="SPEAKER_00"),
                _voice_segment("seg_2", 3, 5, speaker_label="SPEAKER_01"),
                _voice_segment("seg_3", 6, 8, speaker_label="SPEAKER_02"),
            ]
        )

        self.assertEqual(result.level, 3)
        self.assertFalse(result.review_segments)
        self.assertEqual(result.uncertainty_status, "multiple_speakers_uncertain")

    def test_fragmented_voice_segments_do_not_suggest_review(self) -> None:
        result = self._evaluate(
            [
                _voice_segment(f"seg_{index}", index * 2.0, index * 2.0 + 1.0)
                for index in range(6)
            ]
        )

        self.assertEqual(result.level, 3)
        self.assertFalse(result.review_segments)
        self.assertEqual(result.uncertainty_status, "fragmented_voice_segments_uncertain")

    def test_19_medium_critical_audio_event_needs_attention(self) -> None:
        result = self._evaluate([_event_segment("seg_1", 0, 10, "Gunshot, gunfire", 0.46)])

        self.assertEqual(result.level, 1)
        self.assertIn("critical_audio_event_attention", result.matched_rule_codes)

    def test_20_weak_isolated_clues_are_suppressed(self) -> None:
        result = self._evaluate(
            [
                _event_segment("seg_1", 0, 4, "Shout", 0.56),
                _event_segment("seg_2", 40, 44, "Bang", 0.51),
            ]
        )

        self.assertEqual(result.level, 3)
        self.assertFalse(result.review_segments)

    def test_sustained_infant_crying_suggests_review_not_level_one(self) -> None:
        result = self._evaluate(
            [
                _event_segment("seg_1", 0, 10, "Baby cry, infant cry", 0.75),
                _event_segment("seg_2", 5, 12.147, "Baby cry, infant cry", 0.66),
            ]
        )

        self.assertEqual(result.level, 2)
        self.assertIn("infant_crying_sustained_review", result.matched_rule_codes)

    def test_default_review_reason_does_not_expose_rule_state(self) -> None:
        result = self._evaluate([_event_segment("seg_1", 0, 3, "Screaming", 0.62)])

        self.assertEqual(result.level, 2)
        self.assertTrue(result.review_segments)
        self.assertNotIn("未触发当前关注规则", result.review_segments[0]["reason"])
        self.assertIn("建议复核", result.review_segments[0]["reason"])

    def test_internal_trace_fields_are_populated(self) -> None:
        result = self._evaluate(
            [
                _event_segment("seg_1", 0, 5, "Screaming", 0.64),
                _event_segment("seg_2", 4, 9, "Bang", 0.56),
            ]
        )

        self.assertEqual(result.priority, 1)
        self.assertIn("screaming_with_impact", result.matched_composite_insights)
        self.assertTrue(result.high_risk_time_ranges)
        self.assertTrue(result.evidence_summary)
        self.assertIn("unsupportedFeatureInputs", result.debug_info)

    def test_all_condition_requires_every_child_condition(self) -> None:
        config = yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))
        config["attentionRules"] = [
            {
                "code": "scream_high_and_repeated",
                "insightType": "suspected_screaming",
                "all": [
                    {"maxScoreGte": 0.75},
                    {"occurrenceCountGte": 2},
                ],
                "reasonTemplate": "{startSec}s-{endSec}s 高分尖叫重复出现。",
            }
        ]
        rules = AttentionRulesEngine(config)

        single = rules.evaluate([_event_segment("seg_1", 0, 10, "Screaming", 0.8)])
        repeated = rules.evaluate(
            [
                _event_segment("seg_1", 0, 5, "Screaming", 0.8),
                _event_segment("seg_2", 7, 12, "Screaming", 0.78),
            ]
        )

        self.assertEqual(single.level, 2)
        self.assertEqual(repeated.level, 1)

    def test_level_three_api_payload_omits_review_segments(self) -> None:
        payload = PostcallJobResultData.model_validate(
            {
                "jobId": "job_1",
                "jjdh": "JJD_1",
                "state": "completed",
                "level": 3,
                "levelName": "暂无明显线索",
            }
        ).model_dump(mode="json", exclude_none=True)

        self.assertNotIn("reviewSegments", payload)

    def test_level_one_api_payload_requires_review_segments(self) -> None:
        with self.assertRaises(ValidationError):
            PostcallJobResultData.model_validate(
                {
                    "jobId": "job_1",
                    "jjdh": "JJD_1",
                    "state": "completed",
                    "level": 1,
                    "levelName": "需要关注",
                }
            )

    def test_api_review_segment_contains_time_range_and_result_only(self) -> None:
        payload = PostcallJobResultData.model_validate(
            {
                "jobId": "job_1",
                "jjdh": "JJD_1",
                "state": "completed",
                "level": 1,
                "levelName": "需要关注",
                "reviewSegments": [
                    {"startSec": 0.0, "endSec": 12.147, "result": "疑似哭泣"}
                ],
            }
        ).model_dump(mode="json", exclude_none=True)

        self.assertEqual(
            payload["reviewSegments"],
            [{"startSec": 0.0, "endSec": 12.147, "result": "疑似哭泣"}],
        )

        with self.assertRaises(ValidationError):
            PostcallJobResultData.model_validate(
                {
                    "jobId": "job_1",
                    "jjdh": "JJD_1",
                    "state": "completed",
                    "level": 1,
                    "levelName": "需要关注",
                    "reviewSegments": [
                        {
                            "startSec": 0.0,
                            "endSec": 12.147,
                            "result": "疑似哭泣",
                            "reason": "内部原因不对外返回",
                        }
                    ],
                }
            )


if __name__ == "__main__":
    unittest.main()
