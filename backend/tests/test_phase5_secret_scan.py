import sys
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from scripts.interactive_validation import TRACKED_SECRET_PATTERNS  # noqa: E402


def _matches(value: str) -> bool:
    return any(pattern.search(value) for pattern in TRACKED_SECRET_PATTERNS)


def test_phase5_secret_patterns_cover_provider_and_authorization_formats() -> None:
    assert _matches("nvapi-" + "A" * 32)
    assert _matches("sk-proj-" + "B" * 32)
    assert _matches("Authorization: Bearer " + "C" * 32)
    assert _matches("x-api-key=" + "D" * 32)


def test_phase5_secret_patterns_allow_documentation_placeholders() -> None:
    assert not _matches("LLM_API_KEY=replace-with-a-rotated-nvidia-api-key")
    assert not _matches("Authorization: Bearer <LLM_API_KEY>")
