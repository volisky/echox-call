from __future__ import annotations

import unittest

from echox_call.console.annotations import (
    EmotionAnnotationError,
    build_soft_label_intervals,
    parse_annotation_submission,
)


def _valid_form() -> dict[str, list[str]]:
    return {
        "audio_usable": ["1"],
        "segment_count": ["1"],
        "segment_0_start": ["1.25"],
        "segment_0_end": ["4.5"],
        "segment_0_primary": ["Anger"],
        "segment_0_secondary": ["Fear"],
        "segment_0_confidence": ["3"],
    }


class EmotionAnnotationSubmissionTest(unittest.TestCase):
    def test_accepts_segmented_primary_and_secondary_emotions(self) -> None:
        result = parse_annotation_submission(_valid_form())

        self.assertTrue(result.audio_usable)
        self.assertEqual(len(result.segments), 1)
        self.assertEqual(result.segments[0].start_sec, 1.25)
        self.assertEqual(result.segments[0].end_sec, 4.5)
        self.assertEqual(result.segments[0].primary_emotion, "Anger")
        self.assertEqual(result.segments[0].secondary_emotions, ("Fear",))

    def test_rejects_primary_emotion_repeated_as_secondary(self) -> None:
        form = _valid_form()
        form["segment_0_secondary"] = ["Anger"]

        with self.assertRaises(EmotionAnnotationError):
            parse_annotation_submission(form)

    def test_accepts_unusable_audio_without_segments(self) -> None:
        result = parse_annotation_submission({"segment_count": ["0"]})

        self.assertFalse(result.audio_usable)
        self.assertEqual(result.segments, ())

    def test_requires_a_segment_when_audio_is_usable(self) -> None:
        with self.assertRaises(EmotionAnnotationError):
            parse_annotation_submission({"audio_usable": ["1"], "segment_count": ["0"]})

    def test_rejects_non_finite_segment_time(self) -> None:
        form = _valid_form()
        form["segment_0_end"] = ["nan"]

        with self.assertRaises(EmotionAnnotationError):
            parse_annotation_submission(form)

    def test_accepts_five_level_confidence(self) -> None:
        form = _valid_form()
        form["segment_0_confidence"] = ["5"]

        result = parse_annotation_submission(form)

        self.assertEqual(result.segments[0].annotator_confidence, 5)

    def test_rejects_confidence_above_five(self) -> None:
        form = _valid_form()
        form["segment_0_confidence"] = ["6"]

        with self.assertRaises(EmotionAnnotationError):
            parse_annotation_submission(form)


class EmotionSoftLabelAggregationTest(unittest.TestCase):
    def test_splits_crossing_segments_and_averages_shared_interval(self) -> None:
        intervals = build_soft_label_intervals(
            [
                _aggregation_row("annotator-a", 0, 8, "Anger", 5),
                _aggregation_row("annotator-b", 4, 12, "Fear", 5),
            ]
        )

        self.assertEqual([(item["start_sec"], item["end_sec"]) for item in intervals], [(0.0, 4.0), (4.0, 8.0), (8.0, 12.0)])
        self.assertEqual(intervals[0]["soft_labels"]["Anger"], 1.0)
        self.assertEqual(intervals[1]["soft_labels"]["Anger"], 0.5)
        self.assertEqual(intervals[1]["soft_labels"]["Fear"], 0.5)
        self.assertEqual(intervals[2]["soft_labels"]["Fear"], 1.0)

    def test_uses_five_level_confidence_as_average_weight(self) -> None:
        intervals = build_soft_label_intervals(
            [
                _aggregation_row("annotator-a", 0, 5, "Anger", 5),
                _aggregation_row("annotator-b", 0, 5, "Fear", 1),
            ]
        )

        self.assertAlmostEqual(intervals[0]["soft_labels"]["Anger"], 5 / 6)
        self.assertAlmostEqual(intervals[0]["soft_labels"]["Fear"], 1 / 6)
        self.assertAlmostEqual(intervals[0]["sample_weight"], 0.6)


def _aggregation_row(
    annotator: str,
    start_sec: float,
    end_sec: float,
    emotion: str,
    confidence: int,
) -> dict[str, object]:
    return {
        "audio_id": "audio-1",
        "audio_path": "/audio/example.wav",
        "original_filename": "example.wav",
        "annotation_id": f"session-{annotator}",
        "annotator_username": annotator,
        "start_sec": start_sec,
        "end_sec": end_sec,
        "primary_emotion": emotion,
        "annotator_confidence": confidence,
    }


if __name__ == "__main__":
    unittest.main()
