"""Tests for reading OCR output around a box.

These decide whether the locator experiment measures anything. Scoring a box
against a single reference makes "wrong", "different valid occurrence" and
"the reference was bad" indistinguishable, so the experiment reads the words
under a box instead. If that reading is wrong, so is the comparison.
"""

from src.ocr_occurrences import (
    find_all_occurrences,
    text_inside,
    word_boxes,
    words_inside,
)


def ocr(words, scale=1):
    """Builds pytesseract-shaped DICT output from (text, left, top, w, h, line).

    Mirrors the fields build_words reads, so these tests exercise the real
    parsing rather than a parallel implementation.
    """
    return {
        "text": [w[0] for w in words],
        "left": [w[1] for w in words],
        "top": [w[2] for w in words],
        "width": [w[3] for w in words],
        "height": [w[4] for w in words],
        "conf": [90] * len(words),
        "block_num": [0] * len(words),
        "par_num": [0] * len(words),
        "line_num": [w[5] for w in words],
        "_scale": scale,
    }


LINE = ocr(
    [
        ("the", 0, 0, 30, 10, 1),
        ("quick", 40, 0, 50, 10, 1),
        ("fox", 100, 0, 30, 10, 1),
        ("the", 0, 20, 30, 10, 2),
        ("slow", 40, 20, 40, 10, 2),
    ]
)


class TestWordBoxes:
    def test_returns_every_word_with_a_box(self):
        assert len(word_boxes(LINE)) == 5

    def test_divides_coordinates_back_down_by_the_ocr_upscale(self):
        # OCR runs on an upscaled image; a locator returns image pixels. If
        # this division is missed every box lands at three times its position.
        upscaled = ocr([("word", 300, 600, 90, 30, 1)], scale=3)
        assert word_boxes(upscaled)[0]["box"] == {
            "left": 100,
            "top": 200,
            "width": 30,
            "height": 10,
        }


class TestFindAllOccurrences:
    def test_finds_a_single_word(self):
        found = find_all_occurrences(LINE, "quick")
        assert len(found) == 1
        assert found[0]["left"] == 40

    def test_finds_every_occurrence_not_just_the_first(self):
        # "the" appears on both lines. Returning one box is what made the
        # first version of the experiment unable to tell a wrong answer from
        # a different-but-valid one.
        assert len(find_all_occurrences(LINE, "the")) == 2

    def test_matches_a_phrase_spanning_several_words(self):
        found = find_all_occurrences(LINE, "quick fox")
        assert len(found) == 1
        # Box must span from the start of "quick" to the end of "fox".
        assert found[0]["left"] == 40
        assert found[0]["left"] + found[0]["width"] == 130

    def test_ignores_case_and_punctuation(self):
        assert len(find_all_occurrences(LINE, "  QUICK!  ")) == 1

    def test_absent_phrase_returns_nothing(self):
        assert find_all_occurrences(LINE, "elephant") == []

    def test_empty_phrase_returns_nothing(self):
        assert find_all_occurrences(LINE, "   ") == []

    def test_does_not_match_across_separate_lines(self):
        # "fox" ends line 1 and "the" starts line 2. They are not adjacent on
        # screen, so "foxthe" must not match.
        assert find_all_occurrences(LINE, "fox the") == []


class TestWordsInside:
    def test_selects_only_words_whose_centres_are_within_the_box(self):
        inside = words_inside(LINE, {"left": 35, "top": -5, "width": 60, "height": 20})
        assert [w["text"] for w in inside] == ["quick"]

    def test_a_box_clipping_a_word_edge_still_selects_it(self):
        # Highlights are drawn a pixel or two tight; requiring full
        # containment would discard the word the box was drawn for.
        inside = words_inside(LINE, {"left": 42, "top": 1, "width": 45, "height": 8})
        assert [w["text"] for w in inside] == ["quick"]

    def test_returns_words_in_reading_order(self):
        inside = words_inside(LINE, {"left": 0, "top": 0, "width": 200, "height": 40})
        assert [w["text"] for w in inside] == ["the", "quick", "fox", "the", "slow"]

    def test_empty_region_selects_nothing(self):
        assert words_inside(LINE, {"left": 500, "top": 500, "width": 10, "height": 10}) == []


class TestTextInside:
    def test_concatenates_normalised_text(self):
        assert text_inside(LINE, {"left": 0, "top": 0, "width": 140, "height": 15}) == "thequickfox"

    def test_a_phrase_can_be_found_in_the_result(self):
        # This is the experiment's headline check: does the box contain the
        # phrase, regardless of which occurrence OCR would have picked.
        assert "quickfox" in text_inside(LINE, {"left": 0, "top": 0, "width": 140, "height": 15})

    def test_whole_screen_box_contains_everything(self):
        # Which is why the experiment also bounds how many words a box may
        # contain -- otherwise a full-screen box "finds" every phrase.
        everything = text_inside(LINE, {"left": 0, "top": 0, "width": 1000, "height": 1000})
        assert "the" in everything and "slow" in everything
