"""Deterministic attention-rule evaluation for postcall model outputs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class PostcallAttentionRuleError(RuntimeError):
    """Raised when attention rules are missing or invalid."""


@dataclass(frozen=True)
class AttentionEvaluation:
    rule_version: str
    level: int
    level_name: str
    insights: list[dict[str, Any]]
    review_segments: list[dict[str, Any]]
    matched_rule_codes: list[str]
    attention_insights: list[dict[str, Any]]
    attention_conclusion: str
    priority: int
    key_risk_factors: list[str]
    matched_composite_insights: list[str]
    suppressed_insights: list[dict[str, Any]]
    conflict_status: str
    conflict_reason: str
    uncertainty_status: str
    audio_quality_status: str
    high_risk_time_ranges: list[dict[str, Any]]
    recommended_review_time_ranges: list[dict[str, Any]]
    evidence_summary: list[dict[str, Any]]
    confidence_summary: dict[str, Any]
    debug_info: dict[str, Any]


@dataclass(frozen=True)
class _AtomicInsight:
    insight_type: str
    insight_name: str
    segment_id: str
    start_sec: float
    end_sec: float
    source_field: str
    name_en: str
    name_zh: str
    score: float


class AttentionRulesEngine:
    """Convert raw public timeline segments into rule-based review clues."""

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self.rule_version = _required_text(config, "version")
        self.merge_gap_sec = _optional_float(config, "mergeGapSec", 5.0)
        self.near_window_sec = _optional_float(config, "nearWindowSec", 10.0)
        self._confidence = _required_dict(config, "confidence")
        self._insight_types = _required_dict(config, "insightTypes")
        self._composite_insights = _optional_list(config, "compositeInsights", [])
        self._attention_rules = _required_list(config, "attentionRules")
        self._suppression_rules = _optional_list(config, "suppressionRules", [])
        self._conflict_rules = _optional_list(config, "conflictRules", [])
        self._uncertainty_rules = _optional_list(config, "uncertaintyRules", [])
        self._overall_attention_aggregation = _optional_dict(
            config,
            "overallAttentionAggregation",
            {},
        )
        self._output_schema = _optional_dict(config, "outputSchema", {})
        self._validate_config()

    def evaluate(self, timeline: list[dict[str, Any]]) -> AttentionEvaluation:
        atoms = self._extract_atoms(timeline)
        insights = self._aggregate_atoms(atoms)
        insights.extend(self._build_composite_insights(insights))
        insights.extend(
            self._build_configured_relation_insights(
                self._conflict_rules,
                insights,
                rule_category="conflict",
            )
        )
        insights.extend(self._build_timeline_uncertainty_insights(timeline))
        insights.sort(
            key=lambda insight: (
                insight["startSec"],
                insight["endSec"],
                insight["insightType"],
            ),
        )
        self._apply_attention_rules(insights)

        suppressed_insights = [
            insight
            for insight in insights
            if self._is_suppressed(insight, insights)
        ]
        suppressed_ids = {id(insight) for insight in suppressed_insights}
        visible_insights = [
            insight
            for insight in insights
            if id(insight) not in suppressed_ids
        ]
        attention_insights = [
            insight for insight in visible_insights if _insight_level(insight) == 1
        ]
        public_insights = [_build_public_insight(insight) for insight in visible_insights]
        public_suppressed_insights = [
            _build_public_insight(insight) for insight in suppressed_insights
        ]
        review_segments = _build_review_segments(self._select_review_insights(visible_insights))
        level, level_name = _attention_level(visible_insights)
        attention_conclusion = _attention_conclusion(level)
        matched_rule_codes = sorted(
            {
                code
                for insight in visible_insights
                if _insight_level(insight) in {1, 2}
                for code in insight["matchedRuleCodes"]
            }
        )
        conflict_status, conflict_reason = _category_status(
            visible_insights,
            "conflict",
            default_status="none",
        )
        uncertainty_status, _uncertainty_reason = _category_status(
            visible_insights,
            "uncertainty",
            default_status="normal",
        )
        return AttentionEvaluation(
            rule_version=self.rule_version,
            level=level,
            level_name=level_name,
            insights=public_insights,
            review_segments=review_segments,
            matched_rule_codes=matched_rule_codes,
            attention_insights=attention_insights,
            attention_conclusion=attention_conclusion,
            priority=level,
            key_risk_factors=_key_risk_factors(review_segments, visible_insights),
            matched_composite_insights=_matched_composite_insights(visible_insights),
            suppressed_insights=public_suppressed_insights,
            conflict_status=conflict_status,
            conflict_reason=conflict_reason,
            uncertainty_status=uncertainty_status,
            audio_quality_status="unknown",
            high_risk_time_ranges=_time_ranges_for_level(visible_insights, 1),
            recommended_review_time_ranges=_time_ranges_for_level(visible_insights, 2),
            evidence_summary=_evidence_summary(review_segments),
            confidence_summary=_confidence_summary(visible_insights, suppressed_insights),
            debug_info={
                "ruleVersion": self.rule_version,
                "timelineSegmentCount": len(timeline),
                "atomicInsightCount": len([item for item in insights if item.get("ruleCategory") == "atomic"]),
                "visibleInsightCount": len(visible_insights),
                "suppressedInsightCount": len(suppressed_insights),
                "unsupportedFeatureInputs": [
                    "silenceRatio",
                    "lowVolumeRatio",
                    "speechRate",
                    "pausePattern",
                    "snr",
                    "overlapSpeechRatio",
                ],
                "overallAttentionAggregation": self._overall_attention_aggregation,
                "outputSchema": self._output_schema,
            },
        )

    def _validate_config(self) -> None:
        if self.merge_gap_sec < 0:
            raise PostcallAttentionRuleError("mergeGapSec must be greater than or equal to 0")
        if self.near_window_sec < 0:
            raise PostcallAttentionRuleError("nearWindowSec must be greater than or equal to 0")

        _optional_float(self._confidence, "highScoreGte", 0.75)
        _optional_float(self._confidence, "mediumScoreGte", 0.55)

        for insight_type, definition in self._insight_types.items():
            if not isinstance(insight_type, str) or not insight_type.strip():
                raise PostcallAttentionRuleError("insightTypes keys must be non-blank strings")
            if not isinstance(definition, dict):
                raise PostcallAttentionRuleError(f"insightTypes.{insight_type} must be an object")
            _required_text(definition, "insightName", path=f"insightTypes.{insight_type}")
            source_field = _required_text(
                definition,
                "sourceField",
                path=f"insightTypes.{insight_type}",
            )
            if source_field not in {
                "audioEventScores",
                "voiceEmotionScores",
                "voiceEmotionDimensions",
            }:
                raise PostcallAttentionRuleError(
                    f"insightTypes.{insight_type}.sourceField is not supported: {source_field}"
                )
            _validate_score_thresholds(definition, path=f"insightTypes.{insight_type}")
            if source_field == "voiceEmotionDimensions":
                _required_text(
                    definition,
                    "dimensionKey",
                    path=f"insightTypes.{insight_type}",
                )
            else:
                labels = _required_list(definition, "labels", path=f"insightTypes.{insight_type}")
                if not all(isinstance(label, str) and label.strip() for label in labels):
                    raise PostcallAttentionRuleError(
                        f"insightTypes.{insight_type}.labels must contain non-blank strings"
                    )

        for definition in self._composite_insights:
            if not isinstance(definition, dict):
                raise PostcallAttentionRuleError("compositeInsights[] must be objects")
            composite_type = _required_text(definition, "insightType", path="compositeInsights[]")
            if composite_type in self._insight_types:
                raise PostcallAttentionRuleError(
                    f"compositeInsights[].insightType duplicates an atomic insight: {composite_type}"
                )
            _required_text(definition, "insightName", path=f"compositeInsights.{composite_type}")
            source_types = _required_list(
                definition,
                "sourceInsightTypes",
                path=f"compositeInsights.{composite_type}",
            )
            if not source_types:
                raise PostcallAttentionRuleError(
                    f"compositeInsights.{composite_type}.sourceInsightTypes cannot be empty"
                )
            for source_type in source_types:
                if source_type not in self._insight_types:
                    raise PostcallAttentionRuleError(
                        f"compositeInsights.{composite_type}.sourceInsightTypes "
                        f"references unknown insightType: {source_type}"
                    )
            required_types = _optional_list(definition, "requiredInsightTypes", [])
            for required_type in required_types:
                if required_type not in source_types:
                    raise PostcallAttentionRuleError(
                        f"compositeInsights.{composite_type}.requiredInsightTypes "
                        f"must be included in sourceInsightTypes: {required_type}"
                    )
            required_any_types = _optional_list(definition, "requiredAnyInsightTypes", [])
            for required_type in required_any_types:
                if required_type not in source_types:
                    raise PostcallAttentionRuleError(
                        f"compositeInsights.{composite_type}.requiredAnyInsightTypes "
                        f"must be included in sourceInsightTypes: {required_type}"
                    )
            min_distinct_types = _optional_int(definition, "minDistinctTypes", 2)
            if min_distinct_types <= 0:
                raise PostcallAttentionRuleError(
                    f"compositeInsights.{composite_type}.minDistinctTypes must be greater than 0"
                )
            min_occurrences = _optional_int(definition, "minOccurrences", 1)
            if min_occurrences <= 0:
                raise PostcallAttentionRuleError(
                    f"compositeInsights.{composite_type}.minOccurrences must be greater than 0"
                )
            within_sec = _optional_float(definition, "withinSec", self.near_window_sec)
            if within_sec < 0:
                raise PostcallAttentionRuleError(
                    f"compositeInsights.{composite_type}.withinSec must be greater than or equal to 0"
                )
            if _configured_level(definition) == 1:
                _required_text(definition, "matchedRuleCode", path=f"compositeInsights.{composite_type}")

        known_insight_types = set(self._insight_types) | {
            str(definition["insightType"]) for definition in self._composite_insights
        }
        for definition in self._conflict_rules:
            self._validate_relation_definition(
                definition,
                section_name="conflictRules",
                known_insight_types=known_insight_types,
            )
            known_insight_types.add(str(definition["insightType"]))

        for definition in self._uncertainty_rules:
            if not isinstance(definition, dict):
                raise PostcallAttentionRuleError("uncertaintyRules[] must be objects")
            _required_text(definition, "code", path="uncertaintyRules[]")
            _required_text(definition, "insightType", path="uncertaintyRules[]")
            _required_text(definition, "insightName", path="uncertaintyRules[]")
            _configured_level(definition)
            known_insight_types.add(str(definition["insightType"]))

        for rule in self._attention_rules:
            if not isinstance(rule, dict):
                raise PostcallAttentionRuleError("attentionRules[] must be objects")
            _required_text(rule, "code", path="attentionRules[]")
            insight_type = _required_text(rule, "insightType", path="attentionRules[]")
            if insight_type not in known_insight_types:
                raise PostcallAttentionRuleError(
                    f"attentionRules[].insightType is not configured: {insight_type}"
                )
            _configured_level(rule)

        for rule in self._suppression_rules:
            if not isinstance(rule, dict):
                raise PostcallAttentionRuleError("suppressionRules[] must be objects")
            _required_text(rule, "code", path="suppressionRules[]")
            insight_type = _required_text(rule, "insightType", path="suppressionRules[]")
            if insight_type not in known_insight_types:
                raise PostcallAttentionRuleError(
                    f"suppressionRules[].insightType is not configured: {insight_type}"
                )

    def _validate_relation_definition(
        self,
        definition: dict[str, Any],
        *,
        section_name: str,
        known_insight_types: set[str],
    ) -> None:
        if not isinstance(definition, dict):
            raise PostcallAttentionRuleError(f"{section_name}[] must be objects")
        relation_type = _required_text(definition, "insightType", path=f"{section_name}[]")
        if relation_type in self._insight_types:
            raise PostcallAttentionRuleError(
                f"{section_name}.{relation_type}.insightType duplicates an atomic insight"
            )
        _required_text(definition, "insightName", path=f"{section_name}.{relation_type}")
        _required_text(definition, "matchedRuleCode", path=f"{section_name}.{relation_type}")
        source_types = _required_list(
            definition,
            "sourceInsightTypes",
            path=f"{section_name}.{relation_type}",
        )
        if not source_types:
            raise PostcallAttentionRuleError(
                f"{section_name}.{relation_type}.sourceInsightTypes cannot be empty"
            )
        for source_type in source_types:
            if source_type not in known_insight_types:
                raise PostcallAttentionRuleError(
                    f"{section_name}.{relation_type}.sourceInsightTypes "
                    f"references unknown insightType: {source_type}"
                )
        for field in ("requiredInsightTypes", "requiredAnyInsightTypes"):
            for required_type in _optional_list(definition, field, []):
                if required_type not in source_types:
                    raise PostcallAttentionRuleError(
                        f"{section_name}.{relation_type}.{field} must be included in "
                        f"sourceInsightTypes: {required_type}"
                    )
        _configured_level(definition)

    def _extract_atoms(self, timeline: list[dict[str, Any]]) -> list[_AtomicInsight]:
        atoms: list[_AtomicInsight] = []
        for segment in timeline:
            for insight_type, definition in self._insight_types.items():
                atoms.extend(_extract_segment_atoms(segment, insight_type, definition))
        return sorted(atoms, key=lambda atom: (atom.start_sec, atom.end_sec, atom.insight_type))

    def _aggregate_atoms(self, atoms: list[_AtomicInsight]) -> list[dict[str, Any]]:
        grouped: dict[str, list[_AtomicInsight]] = {}
        for atom in atoms:
            grouped.setdefault(atom.insight_type, []).append(atom)

        insights: list[dict[str, Any]] = []
        for insight_type, insight_atoms in grouped.items():
            insight_atoms.sort(key=lambda atom: (atom.start_sec, atom.end_sec, atom.segment_id))
            current: list[_AtomicInsight] = []
            current_end: float | None = None
            for atom in insight_atoms:
                if not current:
                    current = [atom]
                    current_end = atom.end_sec
                    continue
                assert current_end is not None
                if atom.start_sec <= current_end + self.merge_gap_sec:
                    current.append(atom)
                    current_end = max(current_end, atom.end_sec)
                    continue

                insights.append(self._build_aggregated_insight(insight_type, current))
                current = [atom]
                current_end = atom.end_sec
            if current:
                insights.append(self._build_aggregated_insight(insight_type, current))

        return sorted(
            insights,
            key=lambda insight: (
                insight["startSec"],
                insight["endSec"],
                insight["insightType"],
            ),
        )

    def _build_composite_insights(self, insights: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._build_configured_relation_insights(
            self._composite_insights,
            insights,
            rule_category="composite",
        )

    def _build_configured_relation_insights(
        self,
        definitions: list[dict[str, Any]],
        insights: list[dict[str, Any]],
        *,
        rule_category: str,
    ) -> list[dict[str, Any]]:
        composite_insights: list[dict[str, Any]] = []
        for definition in definitions:
            source_types = {str(item) for item in definition["sourceInsightTypes"]}
            candidates = [
                insight
                for insight in insights
                if insight["insightType"] in source_types
            ]
            if not candidates:
                continue

            within_sec = _optional_float(definition, "withinSec", self.near_window_sec)
            for cluster in _cluster_near_insights(candidates, within_sec):
                distinct_types = {insight["insightType"] for insight in cluster}
                required_types = {
                    str(item) for item in _optional_list(definition, "requiredInsightTypes", [])
                }
                if required_types and not required_types.issubset(distinct_types):
                    continue
                required_any_types = {
                    str(item)
                    for item in _optional_list(definition, "requiredAnyInsightTypes", [])
                }
                if required_any_types and distinct_types.isdisjoint(required_any_types):
                    continue
                min_distinct_types = _optional_int(definition, "minDistinctTypes", 2)
                if len(distinct_types) < min_distinct_types:
                    continue
                min_occurrences = _optional_int(definition, "minOccurrences", 1)
                if _cluster_occurrence_count(cluster) < min_occurrences:
                    continue

                composite_insights.append(
                    self._build_composite_insight(
                        definition,
                        cluster,
                        distinct_types,
                        rule_category=rule_category,
                    )
                )

        return composite_insights

    def _build_timeline_uncertainty_insights(
        self,
        timeline: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        insights: list[dict[str, Any]] = []
        speaker_labels = {
            str(segment.get("speakerLabel")).strip()
            for segment in timeline
            if str(segment.get("speakerLabel") or "").strip()
        }
        voice_segments = [
            segment
            for segment in timeline
            if segment.get("voiceEmotionScores") or segment.get("voiceEmotionDimensions")
        ]
        short_voice_segments = [
            segment
            for segment in voice_segments
            if _segment_duration(segment) is not None and _segment_duration(segment) <= 1.5
        ]
        for rule in self._uncertainty_rules:
            trigger = str(rule.get("trigger") or "").strip()
            if trigger == "speakerCountGte":
                threshold = _optional_int(rule, "speakerCountGte", 3)
                if len(speaker_labels) >= threshold:
                    insights.append(
                        self._build_manual_insight(
                            rule,
                            timeline,
                            occurrence_count=len(speaker_labels),
                            total_duration_sec=_timeline_duration(timeline),
                        )
                    )
            elif trigger == "shortVoiceSegmentDensityGte":
                threshold = _optional_int(rule, "shortVoiceSegmentCountGte", 5)
                if len(short_voice_segments) >= threshold:
                    insights.append(
                        self._build_manual_insight(
                            rule,
                            short_voice_segments,
                            occurrence_count=len(short_voice_segments),
                            total_duration_sec=_timeline_duration(short_voice_segments),
                        )
                    )
        return insights

    def _build_manual_insight(
        self,
        definition: dict[str, Any],
        segments: list[dict[str, Any]],
        *,
        occurrence_count: int,
        total_duration_sec: float,
    ) -> dict[str, Any]:
        start_sec = min((_maybe_float(segment.get("startSec")) or 0.0) for segment in segments) if segments else 0.0
        end_sec = max((_maybe_float(segment.get("endSec")) or start_sec) for segment in segments) if segments else start_sec
        insight = {
            "insightType": definition["insightType"],
            "insightName": definition["insightName"],
            "ruleCategory": "uncertainty",
            "level": _configured_level(definition),
            "startSec": _round_time(start_sec),
            "endSec": _round_time(end_sec),
            "occurrenceCount": occurrence_count,
            "totalDurationSec": _round_time(total_duration_sec),
            "maxScore": 0.0,
            "avgScore": 0.0,
            "confidence": "medium",
            "reason": "",
            "matchedRuleCodes": [str(definition["code"])],
            "evidence": [],
        }
        insight["reason"] = _format_reason(str(definition.get("reasonTemplate") or ""), insight)
        return insight

    def _build_composite_insight(
        self,
        definition: dict[str, Any],
        source_insights: list[dict[str, Any]],
        distinct_types: set[str],
        *,
        rule_category: str,
    ) -> dict[str, Any]:
        evidence = _dedupe_evidence(
            evidence
            for insight in source_insights
            for evidence in insight.get("evidence", [])
            if isinstance(evidence, dict)
        )
        scores = [
            _maybe_float(item.get("score"))
            for item in evidence
            if isinstance(item, dict) and _maybe_float(item.get("score")) is not None
        ]
        if not scores:
            scores = [float(insight["maxScore"]) for insight in source_insights]
        max_score = max(scores)
        level = _configured_level(definition)
        matched_rule_code = str(definition.get("matchedRuleCode") or "").strip()
        insight = {
            "insightType": definition["insightType"],
            "insightName": definition["insightName"],
            "ruleCategory": rule_category,
            "level": level,
            "startSec": _round_time(min(float(insight["startSec"]) for insight in source_insights)),
            "endSec": _round_time(max(float(insight["endSec"]) for insight in source_insights)),
            "occurrenceCount": len(evidence) if evidence else len(source_insights),
            "totalDurationSec": _round_time(
                _union_duration_from_intervals(
                    (
                        float(item["startSec"]),
                        float(item["endSec"]),
                    )
                    for item in evidence
                    if "startSec" in item and "endSec" in item
                )
                if evidence
                else _union_duration_from_intervals(
                    (float(item["startSec"]), float(item["endSec"])) for item in source_insights
                )
            ),
            "maxScore": _round_score(max_score),
            "avgScore": _round_score(sum(scores) / len(scores)),
            "reviewPriority": _optional_int(definition, "reviewPriority", 50),
            "confidence": _confidence(
                max_score,
                high_gte=_optional_float(self._confidence, "highScoreGte", 0.75),
                medium_gte=_optional_float(self._confidence, "mediumScoreGte", 0.55),
            ),
            "reason": "",
            "matchedRuleCodes": [matched_rule_code] if matched_rule_code else [],
            "evidence": evidence,
            "distinctTypeCount": len(distinct_types),
        }
        insight["reason"] = _format_reason(
            str(definition.get("reasonTemplate") or ""),
            insight,
        )
        insight.pop("distinctTypeCount", None)
        return insight

    def _build_aggregated_insight(
        self,
        insight_type: str,
        atoms: list[_AtomicInsight],
    ) -> dict[str, Any]:
        scores = [atom.score for atom in atoms]
        max_score = max(scores)
        return {
            "insightType": insight_type,
            "insightName": atoms[0].insight_name,
            "ruleCategory": "atomic",
            "level": 2,
            "startSec": _round_time(min(atom.start_sec for atom in atoms)),
            "endSec": _round_time(max(atom.end_sec for atom in atoms)),
            "occurrenceCount": len(atoms),
            "totalDurationSec": _round_time(_union_duration(atoms)),
            "maxScore": _round_score(max_score),
            "avgScore": _round_score(sum(scores) / len(scores)),
            "reviewPriority": 100,
            "confidence": _confidence(
                max_score,
                high_gte=_optional_float(self._confidence, "highScoreGte", 0.75),
                medium_gte=_optional_float(self._confidence, "mediumScoreGte", 0.55),
            ),
            "reason": "",
            "matchedRuleCodes": [],
            "evidence": [_build_evidence(atom) for atom in atoms],
        }

    def _apply_attention_rules(self, insights: list[dict[str, Any]]) -> None:
        for insight in insights:
            for rule in self._attention_rules:
                if rule["insightType"] != insight["insightType"]:
                    continue
                if not self._rule_matches(rule, insight, insights):
                    continue

                rule_level = _configured_level(rule)
                if rule_level < _insight_level(insight):
                    insight["level"] = rule_level
                if rule["code"] not in insight["matchedRuleCodes"]:
                    insight["matchedRuleCodes"].append(rule["code"])
                if not insight["reason"]:
                    insight["reason"] = _format_reason(
                        str(rule.get("reasonTemplate") or ""),
                        insight,
                    )

            if not insight["reason"]:
                insight["reason"] = _default_reason(insight)

    def _is_suppressed(
        self,
        insight: dict[str, Any],
        all_insights: list[dict[str, Any]],
    ) -> bool:
        if _insight_level(insight) == 1:
            return False
        if insight.get("matchedRuleCodes"):
            return False
        for rule in self._suppression_rules:
            if rule["insightType"] != insight["insightType"]:
                continue
            if self._conditions_match(rule, insight, all_insights):
                return True
        return False

    def _select_review_insights(self, visible_insights: list[dict[str, Any]]) -> list[dict[str, Any]]:
        level_one = [insight for insight in visible_insights if _insight_level(insight) == 1]
        if level_one:
            composite_level_one = [
                insight
                for insight in level_one
                if insight["insightType"] not in self._insight_types
            ]
            return sorted(composite_level_one or level_one, key=_review_sort_key)

        level_two = [insight for insight in visible_insights if _insight_level(insight) == 2]
        composite_level_two = [
            insight
            for insight in level_two
            if insight["insightType"] not in self._insight_types
        ]
        return sorted(composite_level_two or level_two, key=_review_sort_key)

    def _rule_matches(
        self,
        rule: dict[str, Any],
        insight: dict[str, Any],
        all_insights: list[dict[str, Any]],
    ) -> bool:
        return self._conditions_match(rule, insight, all_insights)

    def _conditions_match(
        self,
        conditions: dict[str, Any],
        insight: dict[str, Any],
        all_insights: list[dict[str, Any]],
    ) -> bool:
        if "all" in conditions:
            all_conditions = _required_list(conditions, "all", path="attentionRules[].all")
            if not all(
                isinstance(condition, dict)
                and self._conditions_match(condition, insight, all_insights)
                for condition in all_conditions
            ):
                return False
        if "any" in conditions:
            any_conditions = _required_list(conditions, "any", path="attentionRules[].any")
            if not any(
                isinstance(condition, dict)
                and self._conditions_match(condition, insight, all_insights)
                for condition in any_conditions
            ):
                return False
        if "not" in conditions:
            not_conditions = _required_dict(conditions, "not", path="attentionRules[].not")
            if self._conditions_match(not_conditions, insight, all_insights):
                return False
        if "maxScoreGte" in conditions and insight["maxScore"] < float(conditions["maxScoreGte"]):
            return False
        if "maxScoreGt" in conditions and insight["maxScore"] <= float(conditions["maxScoreGt"]):
            return False
        if "maxScoreLte" in conditions and insight["maxScore"] > float(conditions["maxScoreLte"]):
            return False
        if "maxScoreLt" in conditions and insight["maxScore"] >= float(conditions["maxScoreLt"]):
            return False
        if "occurrenceCountGte" in conditions and insight["occurrenceCount"] < int(
            conditions["occurrenceCountGte"]
        ):
            return False
        if "occurrenceCountLte" in conditions and insight["occurrenceCount"] > int(
            conditions["occurrenceCountLte"]
        ):
            return False
        if "occurrenceCountLt" in conditions and insight["occurrenceCount"] >= int(
            conditions["occurrenceCountLt"]
        ):
            return False
        if "totalDurationSecGte" in conditions and insight["totalDurationSec"] < float(
            conditions["totalDurationSecGte"]
        ):
            return False
        if "totalDurationSecLte" in conditions and insight["totalDurationSec"] > float(
            conditions["totalDurationSecLte"]
        ):
            return False
        if "totalDurationSecLt" in conditions and insight["totalDurationSec"] >= float(
            conditions["totalDurationSecLt"]
        ):
            return False
        if "nearInsightTypes" in conditions:
            near_types = conditions["nearInsightTypes"]
            if not isinstance(near_types, list):
                raise PostcallAttentionRuleError("nearInsightTypes must be a list")
            within_sec = float(conditions.get("withinSec", self.near_window_sec))
            if not _has_near_insight(insight, all_insights, near_types, within_sec):
                return False
        if "notNearInsightTypes" in conditions:
            near_types = conditions["notNearInsightTypes"]
            if not isinstance(near_types, list):
                raise PostcallAttentionRuleError("notNearInsightTypes must be a list")
            within_sec = float(conditions.get("withinSec", self.near_window_sec))
            if _has_near_insight(insight, all_insights, near_types, within_sec):
                return False
        return True


def load_attention_rules(path: Path) -> AttentionRulesEngine:
    if not path.exists():
        raise PostcallAttentionRuleError(f"attention rules file does not exist: {path}")
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PostcallAttentionRuleError(f"attention rules YAML is invalid: {path}") from exc
    if not isinstance(config, dict):
        raise PostcallAttentionRuleError(f"attention rules YAML root must be an object: {path}")
    return AttentionRulesEngine(config)


def _extract_segment_atoms(
    segment: dict[str, Any],
    insight_type: str,
    definition: dict[str, Any],
) -> list[_AtomicInsight]:
    source_field = definition["sourceField"]
    if source_field == "audioEventScores":
        return _extract_named_score_atoms(
            segment,
            insight_type,
            definition,
            source_field=source_field,
            name_en_field="eventNameEn",
            name_zh_field="eventNameZh",
        )
    if source_field == "voiceEmotionScores":
        return _extract_named_score_atoms(
            segment,
            insight_type,
            definition,
            source_field=source_field,
            name_en_field="emotionNameEn",
            name_zh_field="emotionNameZh",
        )
    if source_field == "voiceEmotionDimensions":
        return _extract_dimension_atoms(segment, insight_type, definition)
    return []


def _extract_named_score_atoms(
    segment: dict[str, Any],
    insight_type: str,
    definition: dict[str, Any],
    *,
    source_field: str,
    name_en_field: str,
    name_zh_field: str,
) -> list[_AtomicInsight]:
    allowed_names = {label.strip() for label in definition["labels"]}
    atoms: list[_AtomicInsight] = []
    for item in segment.get(source_field, []):
        if not isinstance(item, dict):
            continue
        name_en = str(item.get(name_en_field) or "").strip()
        if name_en not in allowed_names:
            continue
        score = _maybe_float(item.get("score"))
        if score is None or not _score_matches(definition, score):
            continue
        atom = _build_atom(
            segment,
            insight_type,
            definition,
            source_field=source_field,
            name_en=name_en,
            name_zh=str(item.get(name_zh_field) or name_en).strip(),
            score=score,
        )
        if atom is not None:
            atoms.append(atom)
    return atoms


def _extract_dimension_atoms(
    segment: dict[str, Any],
    insight_type: str,
    definition: dict[str, Any],
) -> list[_AtomicInsight]:
    dimensions = segment.get("voiceEmotionDimensions")
    if not isinstance(dimensions, dict):
        return []
    dimension_key = definition["dimensionKey"]
    item = dimensions.get(dimension_key)
    if not isinstance(item, dict):
        return []
    value = _maybe_float(item.get("value"))
    if value is None or not _score_matches(definition, value):
        return []
    atom = _build_atom(
        segment,
        insight_type,
        definition,
        source_field="voiceEmotionDimensions",
        name_en=str(item.get("dimensionNameEn") or dimension_key).strip(),
        name_zh=str(item.get("dimensionNameZh") or dimension_key).strip(),
        score=value,
    )
    return [atom] if atom is not None else []


def _build_atom(
    segment: dict[str, Any],
    insight_type: str,
    definition: dict[str, Any],
    *,
    source_field: str,
    name_en: str,
    name_zh: str,
    score: float,
) -> _AtomicInsight | None:
    segment_id = str(segment.get("segmentId") or "").strip()
    start_sec = _maybe_float(segment.get("startSec"))
    end_sec = _maybe_float(segment.get("endSec"))
    if not segment_id or start_sec is None or end_sec is None:
        return None
    return _AtomicInsight(
        insight_type=insight_type,
        insight_name=definition["insightName"],
        segment_id=segment_id,
        start_sec=start_sec,
        end_sec=end_sec,
        source_field=source_field,
        name_en=name_en,
        name_zh=name_zh,
        score=min(1.0, max(0.0, score)),
    )


def _build_evidence(atom: _AtomicInsight) -> dict[str, Any]:
    return {
        "segmentId": atom.segment_id,
        "startSec": _round_time(atom.start_sec),
        "endSec": _round_time(atom.end_sec),
        "sourceField": atom.source_field,
        "nameEn": atom.name_en,
        "nameZh": atom.name_zh,
        "score": _round_score(atom.score),
    }


def _build_public_insight(insight: dict[str, Any]) -> dict[str, Any]:
    return {
        "insightType": insight["insightType"],
        "insightName": insight["insightName"],
        "ruleCategory": insight.get("ruleCategory", "atomic"),
        "level": _insight_level(insight),
        "attentionConclusion": _attention_conclusion(_insight_level(insight)),
        "startSec": insight["startSec"],
        "endSec": insight["endSec"],
        "occurrenceCount": insight["occurrenceCount"],
        "totalDurationSec": insight["totalDurationSec"],
        "maxScore": insight["maxScore"],
        "avgScore": insight["avgScore"],
        "confidence": insight["confidence"],
        "reason": insight["reason"],
        "matchedRuleCodes": insight["matchedRuleCodes"],
        "evidence": insight["evidence"],
    }


def _build_review_segments(insights: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        _build_review_segment(insight, index=index)
        for index, insight in enumerate(insights, start=1)
    ]


def _build_review_segment(insight: dict[str, Any], *, index: int) -> dict[str, Any]:
    evidence = [
        item
        for item in insight.get("evidence", [])
        if isinstance(item, dict)
    ]
    return {
        "segmentId": f"review_{index:06d}",
        "startSec": insight["startSec"],
        "endSec": insight["endSec"],
        "title": insight["insightName"],
        "level": _insight_level(insight),
        "levelName": _attention_level_name(_insight_level(insight)),
        "attentionConclusion": _attention_conclusion(_insight_level(insight)),
        "ruleCategory": insight.get("ruleCategory", "atomic"),
        "reason": insight["reason"],
        "confidence": insight["confidence"],
        "matchedRuleCodes": insight["matchedRuleCodes"],
        "audioEvents": _review_evidence_items(evidence, {"audioEventScores"}),
        "voiceStates": _review_evidence_items(
            evidence,
            {"voiceEmotionScores", "voiceEmotionDimensions"},
        ),
        "sourceSegments": _source_segment_ids(evidence),
    }


def _review_evidence_items(
    evidence: list[dict[str, Any]],
    source_fields: set[str],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str, float]] = set()
    for item in evidence:
        if item.get("sourceField") not in source_fields:
            continue
        try:
            score = _round_score(float(item["score"]))
        except (TypeError, ValueError, KeyError):
            continue
        name_en = str(item.get("nameEn") or "").strip()
        name_zh = str(item.get("nameZh") or "").strip()
        if not name_en or not name_zh:
            continue
        key = (name_en, name_zh, score)
        if key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "nameEn": name_en,
                "nameZh": name_zh,
                "score": score,
            }
        )
    return items


def _source_segment_ids(evidence: list[dict[str, Any]]) -> list[str]:
    source_segments: list[str] = []
    seen: set[str] = set()
    for item in evidence:
        segment_id = str(item.get("segmentId") or "").strip()
        if not segment_id or segment_id in seen:
            continue
        seen.add(segment_id)
        source_segments.append(segment_id)
    return source_segments


def _attention_level(insights: list[dict[str, Any]]) -> tuple[int, str]:
    if any(_insight_level(insight) == 1 for insight in insights):
        return 1, "需要关注"
    if any(_insight_level(insight) == 2 for insight in insights):
        return 2, "建议复核"
    return 3, "暂无明显线索"


def _attention_conclusion(level: int) -> str:
    if level == 1:
        return "need_attention"
    if level == 2:
        return "review_suggested"
    return "no_obvious_clue"


def _attention_level_name(level: int) -> str:
    if level == 1:
        return "需要关注"
    if level == 2:
        return "建议复核"
    return "暂无明显线索"


def _insight_level(insight: dict[str, Any]) -> int:
    try:
        level = int(insight.get("level", 2))
    except (TypeError, ValueError):
        return 2
    return level if level in {1, 2, 3} else 2


def _configured_level(definition: dict[str, Any]) -> int:
    conclusion_level: int | None = None
    conclusion = str(definition.get("conclusion") or "").strip()
    if conclusion:
        conclusion_level = {
            "need_attention": 1,
            "review_suggested": 2,
            "no_obvious_clue": 3,
        }.get(conclusion)
        if conclusion_level is None:
            raise PostcallAttentionRuleError(
                "conclusion must be need_attention, review_suggested, or no_obvious_clue"
            )
    try:
        level = int(definition.get("level", conclusion_level or 1))
    except (TypeError, ValueError) as exc:
        raise PostcallAttentionRuleError("level must be 1, 2, or 3") from exc
    if level not in {1, 2, 3}:
        raise PostcallAttentionRuleError("level must be 1, 2, or 3")
    if conclusion_level is not None and level != conclusion_level:
        raise PostcallAttentionRuleError("level and conclusion are inconsistent")
    return level


def _category_status(
    insights: list[dict[str, Any]],
    category: str,
    *,
    default_status: str,
) -> tuple[str, str]:
    category_insights = [
        insight for insight in insights if insight.get("ruleCategory") == category
    ]
    if not category_insights:
        return default_status, ""
    category_insights.sort(key=lambda item: (_insight_level(item), item["startSec"], item["endSec"]))
    selected = category_insights[0]
    matched_codes = selected.get("matchedRuleCodes") or []
    status = str(matched_codes[0]) if matched_codes else str(selected["insightType"])
    return status, str(selected.get("reason") or "")


def _key_risk_factors(
    review_segments: list[dict[str, Any]],
    visible_insights: list[dict[str, Any]],
) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for item in review_segments:
        title = str(item.get("title") or "").strip()
        if title and title not in seen:
            seen.add(title)
            names.append(title)
    for item in visible_insights:
        if _insight_level(item) not in {1, 2}:
            continue
        name = str(item.get("insightName") or "").strip()
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def _matched_composite_insights(insights: list[dict[str, Any]]) -> list[str]:
    matched: list[str] = []
    seen: set[str] = set()
    for insight in insights:
        if insight.get("ruleCategory") == "atomic":
            continue
        insight_type = str(insight.get("insightType") or "").strip()
        if not insight_type or insight_type in seen:
            continue
        seen.add(insight_type)
        matched.append(insight_type)
    return matched


def _review_sort_key(insight: dict[str, Any]) -> tuple[int, float, float, str]:
    try:
        priority = int(insight.get("reviewPriority", 100))
    except (TypeError, ValueError):
        priority = 100
    return (
        priority,
        float(insight.get("startSec") or 0.0),
        float(insight.get("endSec") or 0.0),
        str(insight.get("insightType") or ""),
    )


def _time_ranges_for_level(insights: list[dict[str, Any]], level: int) -> list[dict[str, Any]]:
    ranges: list[dict[str, Any]] = []
    for insight in insights:
        if _insight_level(insight) != level:
            continue
        ranges.append(
            {
                "startSec": insight["startSec"],
                "endSec": insight["endSec"],
                "result": insight["insightName"],
                "reason": insight["reason"],
                "confidence": insight["confidence"],
                "matchedRuleCodes": list(insight.get("matchedRuleCodes") or []),
            }
        )
    return ranges


def _evidence_summary(review_segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "startSec": item["startSec"],
            "endSec": item["endSec"],
            "result": item["title"],
            "sourceSegments": item.get("sourceSegments", []),
            "audioEvents": item.get("audioEvents", []),
            "voiceStates": item.get("voiceStates", []),
        }
        for item in review_segments
    ]


def _confidence_summary(
    visible_insights: list[dict[str, Any]],
    suppressed_insights: list[dict[str, Any]],
) -> dict[str, Any]:
    confidence_counts = {"high": 0, "medium": 0, "low": 0}
    for insight in visible_insights:
        confidence = str(insight.get("confidence") or "low")
        if confidence in confidence_counts:
            confidence_counts[confidence] += 1
    max_score = max((float(insight.get("maxScore") or 0.0) for insight in visible_insights), default=0.0)
    return {
        "visibleInsightCount": len(visible_insights),
        "suppressedInsightCount": len(suppressed_insights),
        "confidenceCounts": confidence_counts,
        "maxScore": _round_score(max_score),
    }


def _cluster_near_insights(
    insights: list[dict[str, Any]],
    within_sec: float,
) -> list[list[dict[str, Any]]]:
    clusters: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_end: float | None = None
    for insight in sorted(
        insights,
        key=lambda item: (item["startSec"], item["endSec"], item["insightType"]),
    ):
        if not current:
            current = [insight]
            current_end = float(insight["endSec"])
            continue
        assert current_end is not None
        if float(insight["startSec"]) <= current_end + within_sec:
            current.append(insight)
            current_end = max(current_end, float(insight["endSec"]))
            continue
        clusters.append(current)
        current = [insight]
        current_end = float(insight["endSec"])
    if current:
        clusters.append(current)
    return clusters


def _cluster_occurrence_count(insights: list[dict[str, Any]]) -> int:
    return sum(int(insight.get("occurrenceCount") or 0) for insight in insights)


def _dedupe_evidence(evidence_items: Any) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for item in evidence_items:
        if not isinstance(item, dict):
            continue
        key = (
            item.get("segmentId"),
            item.get("startSec"),
            item.get("endSec"),
            item.get("sourceField"),
            item.get("nameEn"),
            item.get("score"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(
            {
                "segmentId": item["segmentId"],
                "startSec": item["startSec"],
                "endSec": item["endSec"],
                "sourceField": item["sourceField"],
                "nameEn": item["nameEn"],
                "nameZh": item["nameZh"],
                "score": item["score"],
            }
        )
    return sorted(
        deduped,
        key=lambda item: (
            float(item["startSec"]),
            float(item["endSec"]),
            str(item["segmentId"]),
            str(item["nameEn"]),
        ),
    )


def _has_near_insight(
    insight: dict[str, Any],
    all_insights: list[dict[str, Any]],
    near_types: list[Any],
    within_sec: float,
) -> bool:
    near_type_set = {str(item) for item in near_types}
    for other in all_insights:
        if other is insight:
            continue
        if other["insightType"] not in near_type_set:
            continue
        if _interval_gap(
            insight["startSec"],
            insight["endSec"],
            other["startSec"],
            other["endSec"],
        ) <= within_sec:
            return True
    return False


def _interval_gap(
    left_start: float,
    left_end: float,
    right_start: float,
    right_end: float,
) -> float:
    if left_end < right_start:
        return right_start - left_end
    if right_end < left_start:
        return left_start - right_end
    return 0.0


def _union_duration(atoms: list[_AtomicInsight]) -> float:
    return _union_duration_from_intervals((atom.start_sec, atom.end_sec) for atom in atoms)


def _union_duration_from_intervals(intervals: Any) -> float:
    sorted_intervals = sorted((float(start), float(end)) for start, end in intervals)
    merged: list[tuple[float, float]] = []
    for start, end in sorted_intervals:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        previous_start, previous_end = merged[-1]
        merged[-1] = (previous_start, max(previous_end, end))
    return sum(max(0.0, end - start) for start, end in merged)


def _confidence(score: float, *, high_gte: float, medium_gte: float) -> str:
    if score >= high_gte:
        return "high"
    if score >= medium_gte:
        return "medium"
    return "low"


def _format_reason(template: str, insight: dict[str, Any]) -> str:
    if not template:
        return _default_reason(insight)
    values = {
        "startSec": f"{insight['startSec']:.1f}",
        "endSec": f"{insight['endSec']:.1f}",
        "occurrenceCount": insight["occurrenceCount"],
        "totalDurationSec": f"{insight['totalDurationSec']:.1f}",
        "maxScore": f"{insight['maxScore']:.2f}",
        "avgScore": f"{insight['avgScore']:.2f}",
        "insightName": insight["insightName"],
        "distinctTypeCount": insight.get("distinctTypeCount", ""),
    }
    try:
        return template.format(**values)
    except KeyError as exc:
        raise PostcallAttentionRuleError(
            f"reasonTemplate references unknown field: {exc}"
        ) from exc


def _default_reason(insight: dict[str, Any]) -> str:
    return (
        f"{insight['startSec']:.1f}s-{insight['endSec']:.1f}s "
        f"出现{insight['insightName']}，建议复核。"
    )


def _required_text(value: dict[str, Any], key: str, *, path: str = "") -> str:
    raw = value.get(key)
    if not isinstance(raw, str) or not raw.strip():
        prefix = f"{path}." if path else ""
        raise PostcallAttentionRuleError(f"{prefix}{key} must be a non-blank string")
    return raw.strip()


def _required_dict(value: dict[str, Any], key: str, *, path: str = "") -> dict[str, Any]:
    raw = value.get(key)
    if not isinstance(raw, dict):
        prefix = f"{path}." if path else ""
        raise PostcallAttentionRuleError(f"{prefix}{key} must be an object")
    return raw


def _required_list(value: dict[str, Any], key: str, *, path: str = "") -> list[Any]:
    raw = value.get(key)
    if not isinstance(raw, list):
        prefix = f"{path}." if path else ""
        raise PostcallAttentionRuleError(f"{prefix}{key} must be a list")
    return raw


def _optional_list(value: dict[str, Any], key: str, default: list[Any]) -> list[Any]:
    raw = value.get(key, default)
    if not isinstance(raw, list):
        raise PostcallAttentionRuleError(f"{key} must be a list")
    return raw


def _optional_dict(value: dict[str, Any], key: str, default: dict[str, Any]) -> dict[str, Any]:
    raw = value.get(key, default)
    if not isinstance(raw, dict):
        raise PostcallAttentionRuleError(f"{key} must be an object")
    return raw


def _optional_int(value: dict[str, Any], key: str, default: int) -> int:
    raw = value.get(key, default)
    if isinstance(raw, bool):
        raise PostcallAttentionRuleError(f"{key} must be an integer")
    try:
        number = int(raw)
    except (TypeError, ValueError) as exc:
        raise PostcallAttentionRuleError(f"{key} must be an integer") from exc
    return number


def _optional_float(value: dict[str, Any], key: str, default: float) -> float:
    raw = value.get(key, default)
    number = _maybe_float(raw)
    if number is None:
        raise PostcallAttentionRuleError(f"{key} must be numeric")
    return number


def _validate_score_thresholds(definition: dict[str, Any], *, path: str) -> None:
    has_gte = "scoreGte" in definition
    has_lte = "scoreLte" in definition
    if not has_gte and not has_lte:
        raise PostcallAttentionRuleError(f"{path} must define scoreGte or scoreLte")
    if has_gte:
        _optional_float(definition, "scoreGte", 0)
    if has_lte:
        _optional_float(definition, "scoreLte", 1)


def _score_matches(definition: dict[str, Any], score: float) -> bool:
    if "scoreGte" in definition and score < float(definition["scoreGte"]):
        return False
    if "scoreLte" in definition and score > float(definition["scoreLte"]):
        return False
    return True


def _maybe_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _round_time(value: float) -> float:
    return round(float(value), 3)


def _round_score(value: float) -> float:
    return round(float(value), 6)


def _segment_duration(segment: dict[str, Any]) -> float | None:
    start_sec = _maybe_float(segment.get("startSec"))
    end_sec = _maybe_float(segment.get("endSec"))
    if start_sec is None or end_sec is None:
        return None
    return max(0.0, end_sec - start_sec)


def _timeline_duration(timeline: list[dict[str, Any]]) -> float:
    return _round_time(
        _union_duration_from_intervals(
            (
                _maybe_float(segment.get("startSec")) or 0.0,
                _maybe_float(segment.get("endSec")) or 0.0,
            )
            for segment in timeline
        )
    )
