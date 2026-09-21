# ruff: noqa: E501

import json
import unittest
from pathlib import Path

from scripts.phase8_acceptance import evaluate_evidence, load_json_or_jsonl

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "backend" / "tests" / "fixtures" / "phase8_acoustic_fixtures_v1.json"


class Phase8AcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    def test_manifest_covers_every_required_fixture_family(self):
        families = {fixture["family"] for fixture in self.manifest["fixtures"]}
        self.assertTrue(
            {
                "tts_only",
                "tts_only_room_reverb_delay",
                "near_end_user_only",
                "double_talk_signal_ratio",
                "background_tv_music_conversation",
                "non_speech_impulse",
                "changing_speaker_volume",
                "reference_discontinuity_queue_drop",
                "playback_stop_echo_tail",
            }.issubset(families)
        )
        self.assertTrue(self.manifest["content_policy"]["metadata_only"])

    def test_missing_measurements_are_pending_and_do_not_claim_acceptance(self):
        report = evaluate_evidence({}, self.manifest)

        self.assertEqual(report["gate_status"], "pending")
        self.assertTrue(report["target_results"])
        self.assertTrue(all(target["status"] == "pending" for target in report["target_results"]))

    def test_sensitive_acoustic_content_blocks_the_gate(self):
        report = evaluate_evidence(
            {
                "metadata_only": True,
                "tts_only": {
                    "completed_responses": 100,
                    "false_barge_ins": 0,
                    "transcript": "private speech",
                },
            },
            self.manifest,
        )

        self.assertEqual(report["gate_status"], "blocked")
        self.assertTrue(report["redaction_errors"])

    def test_complete_passing_evidence_passes_all_targets(self):
        fixture_results = [
            {
                "fixture_id": fixture["id"],
                "classification": fixture["expected"]["classification"],
                "session_id": "phase8-session",
                "response_id": f"response-{index}",
                "reason": "fixture_expected_classification",
                "frame_start": index * 160,
                "frame_end": index * 160 + 160,
            }
            for index, fixture in enumerate(self.manifest["fixtures"])
        ]
        report = evaluate_evidence(
            {
                "metadata_only": True,
                "fixture_results": fixture_results,
                "tts_only": {"completed_responses": 100, "false_barge_ins": 0},
                "soak": {"duration_minutes": 30, "false_barge_ins": 0},
                "true_barge_in": {"eligible_attempts": 100, "detected": 95},
                "early_interruption": {"attempted": 20, "failures": 0},
                "latency_ms": {"local_stop_p95": 350, "post_confirmation_stop_request_p95": 50},
                "replacement_turns": {"attempted": 20, "first_word_retained": 20, "duplicates": 0},
                "playback": {"stale_old_response_count": 0},
                "drops": {"pcm_pipeline": 0, "silero": 0, "aec": 0},
                "runtime": {
                    "duplex_turns": 20,
                    "crashes": 0,
                    "anrs": 0,
                    "audio_state_leaks": 0,
                    "stuck_microphone": 0,
                },
                "speech_quality": {"profiles_tested": 4, "failures": 0},
            },
            self.manifest,
        )

        self.assertEqual(report["gate_status"], "pass")
        self.assertTrue(all(target["status"] == "pass" for target in report["target_results"]))

    def test_failed_target_requires_documented_fallback(self):
        payload = {
            "metadata_only": True,
            "tts_only": {"completed_responses": 100, "false_barge_ins": 1},
            "fallback": {
                "documented": True,
                "route_or_profile": "wired_headset",
                "reason": "loudspeaker echo",
            },
        }
        report = evaluate_evidence(payload, self.manifest)

        self.assertEqual(report["gate_status"], "fallback_documented")

    def test_jsonl_loader_preserves_metadata_records(self):
        path = ROOT / "scripts" / ".phase8_test_evidence.jsonl"
        path.write_text(
            '{"metadata_only":true,"session_id":"s1"}\n{"metadata_only":true,"session_id":"s2"}\n',
            encoding="utf-8",
        )
        try:
            self.assertEqual(
                load_json_or_jsonl(path),
                [
                    {"metadata_only": True, "session_id": "s1"},
                    {"metadata_only": True, "session_id": "s2"},
                ],
            )
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
