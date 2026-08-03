"""Unit tests for the locator seam.

Built on synthetic OCR data shaped like pytesseract's DICT output, so these
never invoke Tesseract and stay deterministic.
"""

import dataclasses

import pytest

from src.locator import Location, Locator, OcrLocator


def make_ocr_data(words, scale=1):
    """Builds a pytesseract-shaped dict from (text, left, top, w, h, conf)."""
    data = {
        "text": [w[0] for w in words],
        "left": [w[1] for w in words],
        "top": [w[2] for w in words],
        "width": [w[3] for w in words],
        "height": [w[4] for w in words],
        "conf": [w[5] for w in words],
        "block_num": [1] * len(words),
        "par_num": [1] * len(words),
        "line_num": [1] * len(words),
        "_scale": scale,
    }
    return data


CLEAN = make_ocr_data(
    [
        ("int", 10, 10, 30, 12, 96.0),
        ("count", 45, 10, 50, 12, 94.0),
        ("=", 100, 10, 8, 12, 90.0),
        ("0", 112, 10, 8, 12, 92.0),
    ]
)

ILLEGIBLE = make_ocr_data(
    [
        ("int", 10, 10, 30, 12, 20.0),
        ("count", 45, 10, 50, 12, 18.0),
    ]
)


class TestInterface:
    def test_ocr_locator_satisfies_the_protocol(self):
        assert isinstance(OcrLocator(), Locator)

    def test_rejects_an_out_of_range_threshold(self):
        with pytest.raises(ValueError, match="0.0-1.0"):
            OcrLocator(min_confidence=1.5)


class TestLocate:
    def test_finds_a_present_anchor(self):
        result = OcrLocator().locate(CLEAN, "count")

        assert result is not None
        assert result.source == "ocr"
        assert result.box["left"] == pytest.approx(45, abs=2)

    def test_returns_none_for_absent_anchor(self):
        assert OcrLocator().locate(CLEAN, "zzzznotpresent") is None

    @pytest.mark.parametrize("anchor", ["", "   ", "NONE", "none"])
    def test_treats_empty_and_none_anchors_as_unlocatable(self, anchor):
        # "NONE" is what the prompt tells Gemini to emit when a step has no
        # on-screen target, so it must never be searched for literally.
        assert OcrLocator().locate(CLEAN, anchor) is None

    def test_result_is_immutable(self):
        result = OcrLocator().locate(CLEAN, "count")

        with pytest.raises(dataclasses.FrozenInstanceError):
            result.confidence = 0.1


class TestConfidence:
    def test_legible_text_scores_high(self):
        result = OcrLocator().locate(CLEAN, "count")

        assert result.confidence > 0.9

    def test_illegible_text_scores_low(self):
        result = OcrLocator(min_confidence=0.0).locate(ILLEGIBLE, "count")

        assert result is not None
        assert result.confidence < 0.5

    def test_confidence_is_normalised_to_0_1(self):
        # Tesseract reports 0-100; the interface promises 0.0-1.0.
        result = OcrLocator().locate(CLEAN, "count")

        assert 0.0 <= result.confidence <= 1.0


class TestThreshold:
    def test_declines_a_match_below_the_threshold(self):
        # Declining is the designed behaviour: a highlight in the wrong place
        # actively misdirects the learner, which is worse than none.
        assert OcrLocator(min_confidence=0.9).locate(ILLEGIBLE, "count") is None

    def test_accepts_the_same_match_when_the_threshold_is_lower(self):
        assert OcrLocator(min_confidence=0.1).locate(ILLEGIBLE, "count") is not None

    def test_default_threshold_accepts_everything_found(self):
        # Documented default: word-level confidence filtering is disabled
        # upstream on purpose, and there is no eval set to tune a cutoff from.
        assert OcrLocator().min_confidence == 0.0
        assert OcrLocator().locate(ILLEGIBLE, "count") is not None


class TestLocationShape:
    def test_box_carries_the_four_expected_keys(self):
        result = OcrLocator().locate(CLEAN, "count")

        assert set(result.box) == {"left", "top", "width", "height"}

    def test_location_is_a_location(self):
        assert isinstance(OcrLocator().locate(CLEAN, "count"), Location)
