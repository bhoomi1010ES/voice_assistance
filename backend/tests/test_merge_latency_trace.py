from __future__ import annotations

import json
from pathlib import Path

from scripts.merge_latency_trace import read_source


def test_read_source_accepts_jsonl_and_trace_log_inputs(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "client.jsonl"
    jsonl_path.write_text(json.dumps({"event": "speech_start"}) + "\n", encoding="utf-8")
    log_path = tmp_path / "android.log"
    log_path.write_text(
        'LATENCY_TRACE {"event":"speech_end"}\n',
        encoding="utf-8",
    )

    assert read_source(jsonl_path) == [{"event": "speech_start"}]
    assert read_source(log_path) == [{"event": "speech_end"}]
