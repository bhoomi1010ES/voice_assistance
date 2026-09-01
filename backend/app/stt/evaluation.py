from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class WERResult:
    """Word-level edit counts and WER for one reference/hypothesis pair."""

    substitutions: int
    deletions: int
    insertions: int
    reference_words: int
    wer: float | None


def normalize_transcript(text: str) -> str:
    """Apply deterministic, language-neutral normalization before WER."""

    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = re.sub(r"[^\w\s']", " ", normalized, flags=re.UNICODE)
    normalized = re.sub(r"(?<!\w)'|'(?!\w)", " ", normalized)
    return " ".join(normalized.split())


def calculate_wer(reference: str, hypothesis: str) -> WERResult:
    """Calculate standard WER = (S + D + I) / N.

    A reference with no words has no defined WER. In that case ``wer`` is
    ``None`` and insertions still report false-positive output.
    """

    reference_words = normalize_transcript(reference).split()
    hypothesis_words = normalize_transcript(hypothesis).split()
    reference_count = len(reference_words)
    hypothesis_count = len(hypothesis_words)

    costs = [[0] * (hypothesis_count + 1) for _ in range(reference_count + 1)]
    operations = [["match"] * (hypothesis_count + 1) for _ in range(reference_count + 1)]
    for column in range(1, hypothesis_count + 1):
        costs[0][column] = column
        operations[0][column] = "insert"
    for row in range(1, reference_count + 1):
        costs[row][0] = row
        operations[row][0] = "delete"

    for row in range(1, reference_count + 1):
        for column in range(1, hypothesis_count + 1):
            if reference_words[row - 1] == hypothesis_words[column - 1]:
                costs[row][column] = costs[row - 1][column - 1]
                operations[row][column] = "match"
                continue
            substitution = costs[row - 1][column - 1] + 1
            deletion = costs[row - 1][column] + 1
            insertion = costs[row][column - 1] + 1
            costs[row][column] = min(substitution, deletion, insertion)
            if costs[row][column] == substitution:
                operations[row][column] = "substitute"
            elif costs[row][column] == deletion:
                operations[row][column] = "delete"
            else:
                operations[row][column] = "insert"

    substitutions = deletions = insertions = 0
    row, column = reference_count, hypothesis_count
    while row or column:
        operation = operations[row][column]
        if operation == "substitute":
            substitutions += 1
            row -= 1
            column -= 1
        elif operation == "delete":
            deletions += 1
            row -= 1
        elif operation == "insert":
            insertions += 1
            column -= 1
        else:
            row -= 1
            column -= 1

    wer = (
        round((substitutions + deletions + insertions) / reference_count, 6)
        if reference_count
        else None
    )
    return WERResult(
        substitutions=substitutions,
        deletions=deletions,
        insertions=insertions,
        reference_words=reference_count,
        wer=wer,
    )
