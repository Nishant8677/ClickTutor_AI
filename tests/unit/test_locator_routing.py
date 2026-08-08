"""Tests the choice between the OCR locator and the vision locator.

The rule: OCR wins when it matched the whole phrase, because on readable
screens it is more accurate, tighter and already computed. Vision is asked only
when OCR declines, because it costs a round trip. Neither is asked twice for
the same anchor, and the fallback is bounded.

These assert the routing, not the locators. The measurements behind the rule
live in benchmarks/locator_comparison.json and benchmarks/hostile_locator.json.
"""

from unittest.mock import Mock

from PIL import Image

from src.lesson_engine import MAX_VISION_FALLBACKS, LessonEngine


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

# Passing an Image rather than a path keeps highlight_box from writing files;
# the routing decision happens before that either way.
IMAGE = Image.new("RGB", (200, 100))

VISION_BOX = {"left": 7, "top": 7, "width": 20, "height": 8}


def vision_returning(box):
    return Mock(return_value=Mock(box=box))


def engine(vision_locator=None):
    return LessonEngine(IMAGE, SCREEN, vision_locator=vision_locator)


def step(anchor):
    return {"step": 1, "anchor": anchor, "context": None, "explanation": "x"}


class TestOcrIsPreferred:
    def test_whole_phrase_match_does_not_call_vision(self):
        # OCR is already computed and, on a phrase match, was right 40 times
        # out of 40. Paying for a round trip here would be waste.
        locator = vision_returning(VISION_BOX)
        engine(locator).build_step_highlights([step("Moral of the story")])
        locator.assert_not_called()

    def test_phrase_match_is_used_even_when_vision_is_available(self):
        eng = engine(vision_returning(VISION_BOX))
        assert eng._locate("Moral of the story", None) == {
            "left": 0,
            "top": 0,
            "width": 135,
            "height": 10,
        }


class TestVisionFallback:
    def test_single_word_match_is_refused_and_vision_is_asked(self):
        # "strength" is on screen but the phrase is not. The old behaviour
        # returned the single word's box and reported success.
        locator = vision_returning(VISION_BOX)
        assert engine(locator)._locate("Intelligence is strength", None) == VISION_BOX
        locator.assert_called_once()

    def test_without_a_vision_locator_a_word_match_yields_nothing(self):
        # Drawing nothing beats drawing the wrong thing: a highlight is a claim
        # about what the explanation refers to.
        assert engine()._locate("Intelligence is strength", None) is None

    def test_absent_phrase_falls_back_to_vision(self):
        locator = vision_returning(VISION_BOX)
        assert engine(locator)._locate("elephant", None) == VISION_BOX

    def test_vision_returning_nothing_is_not_an_error(self):
        assert engine(vision_returning(None))._locate("elephant", None) is None


class TestFallbackIsBounded:
    def test_stops_after_the_budget_is_spent(self):
        locator = vision_returning(VISION_BOX)
        steps = [step("elephant") for _ in range(MAX_VISION_FALLBACKS + 3)]
        engine(locator).build_step_highlights(steps)
        assert locator.call_count == MAX_VISION_FALLBACKS

    def test_budget_resets_between_lessons(self):
        locator = vision_returning(VISION_BOX)
        eng = engine(locator)
        eng.build_step_highlights([step("elephant")] * MAX_VISION_FALLBACKS)
        eng.build_step_highlights([step("elephant")])
        assert locator.call_count == MAX_VISION_FALLBACKS + 1

    def test_phrase_matches_do_not_consume_the_budget(self):
        locator = vision_returning(VISION_BOX)
        eng = engine(locator)
        eng.build_step_highlights([step("Moral of the story")] * 5 + [step("elephant")])
        assert locator.call_count == 1


class TestFallbackFailure:
    def test_a_failing_vision_call_leaves_the_step_unhighlighted(self):
        # The lesson still has to render. A network fault must cost a
        # highlight, not the explanation.
        locator = Mock(side_effect=RuntimeError("network down"))
        assert engine(locator)._locate("elephant", None) is None

    def test_a_failing_vision_call_does_not_stop_later_steps(self):
        locator = Mock(side_effect=[RuntimeError("boom"), Mock(box=VISION_BOX)])
        eng = engine(locator)
        results = eng.build_step_highlights([step("elephant"), step("elephant")])
        assert len(results) == 2
        assert locator.call_count == 2
