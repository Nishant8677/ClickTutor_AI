"""Tests that the locator reports how it matched, not just what it matched.

The pass name is the routing signal. Measured across both benchmark corpora,
phrase-level passes were on target 40 times out of 40 and word-level passes 0
times out of 9 -- while every one of those 9 was reported to callers as a
successful lookup. If these names drift, a wrong highlight becomes
indistinguishable from a right one again.
"""

from src.ocr_locator import (
    PASS_EXACT_WORD,
    PASS_LINE_SUBSTRING,
    TRUSTED_PASSES,
    WORD_LEVEL_PASSES,
    find_text,
    find_text_detailed,
)


def ocr(words, scale=1):
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


SCREEN = ocr(
    [
        ("Moral", 0, 0, 40, 10, 1),
        ("of", 45, 0, 15, 10, 1),
        ("the", 65, 0, 25, 10, 1),
        ("story", 95, 0, 40, 10, 1),
        ("strength", 0, 40, 60, 10, 2),
    ]
)


class TestPassReporting:
    def test_whole_phrase_reports_a_phrase_level_pass(self):
        box, which = find_text_detailed(SCREEN, "Moral of the story")
        assert box is not None
        assert which == PASS_LINE_SUBSTRING
        assert which in TRUSTED_PASSES

    def test_one_word_of_a_longer_phrase_reports_a_word_level_pass(self):
        # "strength" is on screen; the rest of the phrase is not. The locator
        # still returns a box -- for the single word, on a different line --
        # and that box is not what the caller asked for. This is the failure
        # that was previously silent.
        box, which = find_text_detailed(SCREEN, "Intelligence is strength")
        assert box is not None
        assert which == PASS_EXACT_WORD
        assert which in WORD_LEVEL_PASSES
        assert which not in TRUSTED_PASSES

    def test_the_returned_box_covers_only_the_matched_word(self):
        # Concretely why the caller must not trust it: the box describes
        # "strength" alone, not the phrase requested.
        box, _ = find_text_detailed(SCREEN, "Intelligence is strength")
        assert box == {"left": 0, "top": 40, "width": 60, "height": 10}

    def test_no_match_reports_no_pass(self):
        assert find_text_detailed(SCREEN, "elephant") == (None, None)

    def test_empty_target_reports_no_pass(self):
        assert find_text_detailed(SCREEN, "") == (None, None)

    def test_the_none_anchor_reports_no_pass(self):
        assert find_text_detailed(SCREEN, "NONE") == (None, None)


class TestBackwardsCompatibility:
    def test_find_text_still_returns_a_bare_box(self):
        # The shipping call sites pass through find_text; adding the pass name
        # must not change what they receive.
        assert (
            find_text(SCREEN, "Moral of the story")
            == find_text_detailed(SCREEN, "Moral of the story")[0]
        )

    def test_find_text_still_returns_none_for_a_miss(self):
        assert find_text(SCREEN, "elephant") is None

    def test_context_still_disambiguates(self):
        screen = ocr(
            [
                ("total", 0, 0, 40, 10, 1),
                ("total", 0, 40, 40, 10, 2),
                ("revenue", 45, 40, 55, 10, 2),
            ]
        )
        box, _ = find_text_detailed(screen, "total", "total revenue")
        assert box is not None
        assert box["top"] == 40
