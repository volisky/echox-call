"""Coverage for the IfCase / CaseReason fields exposed on the result API."""

from __future__ import annotations

import unittest

from pydantic import ValidationError

from echox_call.features.audio_analysis.postcall.llm_repository import (
    resolve_llm_case_reason,
    resolve_llm_if_case,
)
from echox_call.features.audio_analysis.postcall.llm_worker_models import (
    CASE_REASON_SEPARATOR,
    LlmAnalysisOutput,
)
from echox_call.features.audio_analysis.postcall.repository import (
    _backfill_llm_derived_fields,
    _build_llm_partial_overall_result,
)
from echox_call.features.audio_analysis.postcall.schemas import (
    ATTENTION_LEVEL_NAMES,
    InputSnapshot,
    OverallResult,
    VoiceResult,
)


def _llm_output(**overrides: object) -> LlmAnalysisOutput:
    values: dict[str, object] = {
        "level": 1,
        "level_name": "需要关注",
        "case_type_summary": "分析总结：涉及涉敏感物品（管制器具）。",
        "case_type_details": [
            {"caseType": "涉敏感物品（管制器具）", "reason": "转写内容明确提到刀具。"}
        ],
        "high_risk_address_summary": None,
        "high_risk_person_summary": None,
        "secondary_review_confidence": None,
        "secondary_review_reason": None,
        "llm_model": "deepseek-v4-flash",
    }
    values.update(overrides)
    return LlmAnalysisOutput(**values)  # type: ignore[arg-type]


def _overall_result(**overrides: object) -> OverallResult:
    values: dict[str, object] = {
        "level": 2,
        "levelName": "建议复核",
        "summary": ["分析总结：示例。"],
        "voiceResult": VoiceResult(level=None, levelName=None),
        "inputSnapshot": InputSnapshot(),
    }
    values.update(overrides)
    return OverallResult(**values)  # type: ignore[arg-type]


class LlmAnalysisOutputFlagTest(unittest.TestCase):
    def test_if_case_is_one_only_for_level_one(self) -> None:
        self.assertEqual(_llm_output(level=1).if_case, 1)
        self.assertEqual(_llm_output(level=2).if_case, 0)
        self.assertEqual(_llm_output(level=3).if_case, 0)

    def test_case_reason_matches_single_detail(self) -> None:
        output = _llm_output()

        self.assertEqual(output.case_reason, "转写内容明确提到刀具。")
        self.assertEqual(output.case_reason, output.case_type_details[0]["reason"])

    def test_case_reason_joins_multiple_details(self) -> None:
        output = _llm_output(
            case_type_details=[
                {"caseType": "涉敏感物品（管制器具）", "reason": "原因一"},
                {"caseType": "涉校园欺凌", "reason": "原因二"},
            ]
        )

        self.assertEqual(output.case_reason, f"原因一{CASE_REASON_SEPARATOR}原因二")

    def test_case_reason_is_populated_for_every_level(self) -> None:
        self.assertEqual(_llm_output(level=3).case_reason, "转写内容明确提到刀具。")

    def test_case_reason_is_none_without_details(self) -> None:
        self.assertIsNone(_llm_output(case_type_details=[]).case_reason)


class ResolveLlmDerivedFieldTest(unittest.TestCase):
    def test_prefers_persisted_values(self) -> None:
        llm_out = {"level": 2, "IfCase": 1, "CaseReason": "持久化原因"}

        self.assertEqual(resolve_llm_if_case(llm_out), 1)
        self.assertEqual(resolve_llm_case_reason(llm_out), "持久化原因")

    def test_falls_back_to_level_for_legacy_rows(self) -> None:
        self.assertEqual(resolve_llm_if_case({"level": 1}), 1)
        self.assertEqual(resolve_llm_if_case({"level": 2}), 0)
        self.assertEqual(resolve_llm_if_case({"level": 3}), 0)

    def test_accepts_string_levels(self) -> None:
        self.assertEqual(resolve_llm_if_case({"level": "1"}), 1)
        self.assertEqual(resolve_llm_if_case({"level": "3"}), 0)

    def test_treats_boolean_flag_as_flag(self) -> None:
        self.assertEqual(resolve_llm_if_case({"IfCase": True}), 1)
        self.assertEqual(resolve_llm_if_case({"IfCase": False}), 0)

    def test_defaults_to_zero_without_level(self) -> None:
        self.assertEqual(resolve_llm_if_case({}), 0)

    def test_falls_back_to_case_type_details(self) -> None:
        llm_out = {
            "caseTypeDetails": [
                {"caseType": "涉敏感物品（管制器具）", "reason": "原因一"},
                {"caseType": "涉校园欺凌", "reason": "原因二"},
            ]
        }

        self.assertEqual(
            resolve_llm_case_reason(llm_out), f"原因一{CASE_REASON_SEPARATOR}原因二"
        )

    def test_returns_none_without_usable_details(self) -> None:
        self.assertIsNone(resolve_llm_case_reason({}))
        self.assertIsNone(resolve_llm_case_reason({"caseTypeDetails": []}))
        self.assertIsNone(
            resolve_llm_case_reason(
                {"caseTypeDetails": [{"caseType": "涉校园欺凌", "reason": "   "}]}
            )
        )


class OverallResultIfCaseTest(unittest.TestCase):
    def test_defaults_are_zero_and_none(self) -> None:
        result = _overall_result()

        self.assertEqual(result.IfCase, 0)
        self.assertIsNone(result.CaseReason)

    def test_block_omits_none_case_reason_when_dumped(self) -> None:
        payload = _overall_result().model_dump(mode="json", exclude_none=True)

        self.assertEqual(payload["IfCase"], 0)
        self.assertNotIn("CaseReason", payload)

    def test_fields_are_serialized_next_to_level(self) -> None:
        result = _overall_result(
            IfCase=1,
            CaseReason="转写内容明确提到刀具。",
        )
        payload = result.model_dump(mode="json", exclude_none=True)

        self.assertEqual(payload["IfCase"], 1)
        self.assertEqual(payload["CaseReason"], "转写内容明确提到刀具。")
        self.assertEqual(
            list(payload)[:4], ["level", "levelName", "IfCase", "CaseReason"]
        )

    def test_rejects_out_of_range_flag(self) -> None:
        with self.assertRaises(ValidationError):
            _overall_result(IfCase=2)


class BackfillLlmDerivedFieldsTest(unittest.TestCase):
    def test_adds_fields_to_legacy_payload(self) -> None:
        payload = {"overallResult": {"level": 2, "levelName": "建议复核"}}
        llm_out = {
            "level": 1,
            "caseTypeDetails": [{"caseType": "涉校园欺凌", "reason": "原因一"}],
        }

        patched = _backfill_llm_derived_fields(payload, llm_out)

        self.assertEqual(patched["overallResult"]["IfCase"], 1)
        self.assertEqual(patched["overallResult"]["CaseReason"], "原因一")
        self.assertNotIn("IfCase", payload["overallResult"])

    def test_keeps_existing_values(self) -> None:
        payload = {
            "overallResult": {"level": 2, "IfCase": 0, "CaseReason": "已有原因"}
        }

        patched = _backfill_llm_derived_fields(payload, {"level": 1})

        self.assertEqual(patched["overallResult"]["IfCase"], 0)
        self.assertEqual(patched["overallResult"]["CaseReason"], "已有原因")

    def test_returns_payload_unchanged_without_llm_output(self) -> None:
        payload = {"overallResult": {"level": 2}}

        self.assertIs(_backfill_llm_derived_fields(payload, {}), payload)

    def test_ignores_payload_without_overall_result(self) -> None:
        payload = {"state": "processing"}

        self.assertIs(_backfill_llm_derived_fields(payload, {"level": 1}), payload)


class PartialOverallResultFlagTest(unittest.TestCase):
    def test_llm_partial_result_carries_flags(self) -> None:
        result = _build_llm_partial_overall_result(
            llm_out={
                "level": 1,
                "levelName": "需要关注",
                "caseTypeDetails": [
                    {
                        "caseType": "涉敏感物品（管制器具）",
                        "reason": "转写内容明确提到刀具。",
                    }
                ],
            },
            voice_result=None,
            input_snapshot=InputSnapshot(),
            risk_person=None,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.IfCase, 1)
        self.assertEqual(result.CaseReason, "转写内容明确提到刀具。")

    def test_flags_follow_llm_level_not_fused_level(self) -> None:
        result = _build_llm_partial_overall_result(
            llm_out={"level": 3, "levelName": "暂无明显线索"},
            voice_result=VoiceResult(level=1, levelName=ATTENTION_LEVEL_NAMES[1]),
            input_snapshot=InputSnapshot(),
            risk_person=None,
        )

        self.assertIsNotNone(result)
        assert result is not None
        # 融合层被音频提升为 1，IfCase 仍跟随 LLM 文本 level（3 -> 0）。
        self.assertEqual(result.level, 1)
        self.assertEqual(result.IfCase, 0)


if __name__ == "__main__":
    unittest.main()
