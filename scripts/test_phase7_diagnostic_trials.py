from __future__ import annotations

import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
from phase7_diagnostic_trials import build_evidence, parse_diagnostic_line


class Phase7DiagnosticTrialTests(unittest.TestCase):
    def test_parser_keeps_only_metadata_and_full_correlation(self) -> None:
        event = parse_diagnostic_line(
            "I/VoiceAI-Bridge: diagnostic_session_id=diag-1 "
            "BARGE_IN_DECISION event=BARGE_IN_CONFIRMED state=NEAR_END_CONFIRMED "
            "reason=near_end_confirmed response_id=response-1 source_frames=10-33 "
            "inference=4 capture=1000000000-1020000000 reference_ready=true "
            "timestamp_confidence=AUDIO_TIMESTAMP transcript=must_not_escape",
            scenario="double-talk",
        )
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event["event_name"], "BARGE_IN_CONFIRMED")
        self.assertTrue(event["correlation_complete"])
        self.assertNotIn("transcript", event)
        self.assertNotIn("raw", event)

    def test_evidence_has_three_trials_and_redaction_contract(self) -> None:
        event = parse_diagnostic_line(
            "I/VoiceAI-Bridge: diagnostic_session_id=diag-1 "
            "BARGE_IN_DECISION event=BARGE_IN_REJECTED_ECHO state=ECHO_ONLY "
            "reason=echo_similarity_high response_id=response-1 source_frames=1-2 "
            "inference=1 capture=100-200 reference_ready=true "
            "timestamp_confidence=AUDIO_TIMESTAMP",
            scenario="double-talk",
        )
        evidence = build_evidence([event] if event else [], run_id="run-1")
        self.assertTrue(evidence["metadata_only"])
        self.assertEqual([trial["scenario"] for trial in evidence["trials"]], [
            "tts-only",
            "user-only",
            "double-talk",
        ])
        self.assertIn("pcm", evidence["redacted_fields"])
        self.assertEqual(evidence["trials"][2]["correlated_decision_count"], 1)


if __name__ == "__main__":
    unittest.main()
