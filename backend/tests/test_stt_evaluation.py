from app.stt.evaluation import calculate_wer, normalize_transcript


def test_transcript_normalization_is_unicode_and_punctuation_stable() -> None:
    assert normalize_transcript(" Héllo,  WORLD! ") == "héllo world"
    assert normalize_transcript("don't stop") == "don't stop"


def test_wer_reports_standard_edit_counts() -> None:
    result = calculate_wer("one two three", "one too four")
    assert result.substitutions == 2
    assert result.deletions == 0
    assert result.insertions == 0
    assert result.reference_words == 3
    assert result.wer == 0.666667


def test_wer_marks_empty_reference_undefined_but_counts_insertions() -> None:
    result = calculate_wer("", "noise")
    assert result.wer is None
    assert result.reference_words == 0
    assert result.insertions == 1
