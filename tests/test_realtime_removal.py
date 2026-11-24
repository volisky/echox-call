"""Regression tests for the realtime audio stream removal.

Keep these focused on externally visible behavior: the retired gateway route
stays unmounted, and postcall partial results no longer reach into realtime
window storage for call_id jobs.
"""

from __future__ import annotations

import unittest

from echox_call.features.audio_analysis.postcall.repository import (
    _build_partial_overall_result,
)


class RealtimeRemovalTests(unittest.TestCase):
    def test_partial_result_does_not_fall_back_to_realtime_windows(self) -> None:
        """A call_id job with no audio/LLM result yields no partial result.

        Previously the realtime window backfill queried
        postcall_realtime_windows by call_id; that path is gone and this call
        must not touch the database.
        """
        overall = _build_partial_overall_result(
            {
                "audio_completed_at": None,
                "audio_analysis_data": {},
                "llm_state": "processing",
                "llm_output": None,
                "call_id": "test-call-001",
                "raw_payload": {},
            }
        )
        self.assertIsNone(overall)

    def test_gateway_router_unmounted(self) -> None:
        from echox_call.api.app import app

        paths = {route.path for route in app.routes}
        self.assertNotIn("/gateway/asr/push", paths)
        self.assertFalse(any(path.startswith("/gateway") for path in paths))
        self.assertIn("/api/v1/postcall/jobs", paths)
        self.assertIn("/health", paths)


if __name__ == "__main__":
    unittest.main()
