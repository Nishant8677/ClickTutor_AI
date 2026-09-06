"""Tests that the location the engine decided is the one the desktop draws.

The defect these guard against was silent: LessonEngine routed each anchor
through trusted OCR and a capped vision fallback, then threw the answer away,
and the controller looked the anchor up again with the permissive matcher at
draw time. A word-level match the engine had refused came back as a highlight,
and a box the vision fallback had paid for was lost.

Everything runs through a real DesktopController on the offscreen platform,
with the same PIL-image capture the desktop worker produces. The Gemini call
is guarded at every binding the engine and vision locator actually invoke,
and the controller's module-level vision locator is guarded too; any call to
one of them fails the test. The vision fallback the engine tests exercise is
the synthetic locator each test passes to generated() explicitly.
"""

from unittest.mock import Mock

import pytest
from PIL import Image
from PyQt6.QtWidgets import QApplication

import src.desktop.controller as controller_module
import src.lesson_engine as engine_module
import src.tutor as tutor_module
import src.vision_locator as vision_module
from src.desktop.controller import DesktopController
from src.lesson_engine import MAX_VISION_FALLBACKS, LessonEngine
from src.locator import STEP_LOCATION_KEY


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

# The desktop worker hands the engine the captured PIL image, not a path.
IMAGE = Image.new("RGB", (200, 100))

PHRASE = "Moral of the story"
PHRASE_BOX = {"left": 0, "top": 0, "width": 135, "height": 10}
# Only "strength" is on screen, so this matches at word level and nowhere else.
WEAK = "Intelligence is strength"
ABSENT = "elephant"
VISION_BOX = {"left": 7, "top": 47, "width": 20, "height": 8}


def vision_returning(*boxes):
    return Mock(side_effect=[Mock(box=box) for box in boxes])


def step(anchor, number=1):
    return {
        "step": number,
        "title": "t",
        "anchor": anchor,
        "context": None,
        "attention": "rectangle",
        "emphasis": "low",
        "explanation": "x",
    }


def generated(steps, vision=None):
    """Routes the steps through the real engine, as LessonWorker.run does."""
    return LessonEngine(IMAGE, SCREEN, vision_locator=vision).build_step_highlights(steps)


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def _guard(what):
    return Mock(side_effect=AssertionError(f"{what} called from the render path"))


@pytest.fixture
def controller(qt_app, monkeypatch):
    # No lesson-rendering path may reach the model. Navigation in particular
    # used to be free only by accident, because the re-lookup was OCR-only.
    # The engine and vision locator bind generate_content by name at import,
    # so patching src.tutor alone would leave their call sites unguarded.
    guards = {
        (engine_module, "generate_content"): _guard("model"),
        (vision_module, "generate_content"): _guard("model"),
        (tutor_module, "generate_content"): _guard("model"),
        (controller_module, "VISION_LOCATOR"): _guard("vision locator"),
    }
    for (module, name), guard in guards.items():
        monkeypatch.setattr(module, name, guard)
    controller = DesktopController()
    # Declares the capture's size so boxes go through the real fit transform
    # rather than the identity mapper, matching what a capture does.
    controller.overlay.set_source_size(IMAGE.width, IMAGE.height)
    yield controller
    controller.overlay.animation_engine.stop()
    # The engine's error handling can swallow the AssertionError a guard
    # raises, so the raise alone is not a reliable failure. The call count is.
    for (module, name), guard in guards.items():
        assert guard.call_count == 0, f"{module.__name__}.{name} was called {guard.call_count}x"


def teach(controller, steps):
    """Delivers a finished lesson the way the worker's signal does."""
    controller._on_lesson_finished(SCREEN, steps, "answer")


def drawn(controller):
    return [
        (shape.x, shape.y, shape.width, shape.height)
        for shape in controller.overlay.animation_engine.shapes
    ]


def widget_box(controller, box):
    mapped = controller.overlay.mapper.map_box(box)
    return (mapped["left"], mapped["top"], mapped["width"], mapped["height"])


class TestEngineRecordsItsDecision:
    def test_every_step_carries_the_key_even_when_unresolved(self):
        steps = generated([step(PHRASE), step(WEAK), step("NONE")])

        assert all(STEP_LOCATION_KEY in s for s in steps)
        assert steps[0][STEP_LOCATION_KEY] == {"box": PHRASE_BOX, "source": "ocr"}
        assert steps[1][STEP_LOCATION_KEY] is None
        assert steps[2][STEP_LOCATION_KEY] is None

    def test_a_pil_capture_records_the_box_without_a_highlight_file(self):
        # Desktop captures never had a highlighted image (that needs a path
        # to write next to); the location is what the desktop needed all along.
        (only,) = generated([step(PHRASE)])

        assert only["highlighted_image"] is None
        assert only[STEP_LOCATION_KEY]["box"] == PHRASE_BOX

    def test_vision_box_is_recorded_with_its_source(self):
        (only,) = generated([step(WEAK)], vision=vision_returning(VISION_BOX))

        assert only[STEP_LOCATION_KEY] == {"box": VISION_BOX, "source": "vision"}


class TestTrustedOcrReachesTheOverlay:
    def test_phrase_match_is_drawn_where_the_engine_put_it(self, controller):
        teach(controller, generated([step(PHRASE)]))

        assert drawn(controller) == [widget_box(controller, PHRASE_BOX)]

    def test_rendering_does_not_look_the_anchor_up_again(self, controller):
        controller.locator = Mock()

        teach(controller, generated([step(PHRASE)]))

        controller.locator.locate.assert_not_called()


class TestRejectedWeakMatchStaysBlank:
    def test_word_level_match_without_a_fallback_draws_nothing(self, controller):
        teach(controller, generated([step(WEAK)]))

        assert drawn(controller) == []

    def test_word_level_match_whose_fallback_finds_nothing_draws_nothing(self, controller):
        vision = vision_returning(None)

        teach(controller, generated([step(WEAK)], vision=vision))

        assert drawn(controller) == []
        vision.assert_called_once()

    def test_word_level_match_whose_fallback_fails_draws_nothing(self, controller):
        teach(controller, generated([step(WEAK)], vision=Mock(side_effect=RuntimeError("down"))))

        assert drawn(controller) == []

    def test_rendering_cannot_resurrect_the_rejected_match(self, controller):
        # Even a locator that would answer is not asked: the engine's None is
        # the authoritative result for the step.
        controller.locator = Mock()
        controller.locator.locate.return_value = Mock(box=PHRASE_BOX)

        teach(controller, generated([step(WEAK)]))

        assert drawn(controller) == []
        controller.locator.locate.assert_not_called()


class TestVisionResultReachesTheOverlay:
    def test_vision_box_is_drawn_as_returned(self, controller):
        teach(controller, generated([step(WEAK)], vision=vision_returning(VISION_BOX)))

        assert drawn(controller) == [widget_box(controller, VISION_BOX)]

    def test_absent_phrase_located_by_vision_is_drawn(self, controller):
        teach(controller, generated([step(ABSENT)], vision=vision_returning(VISION_BOX)))

        assert drawn(controller) == [widget_box(controller, VISION_BOX)]


class TestNavigationReusesTheDecision:
    def test_next_and_previous_redraw_stored_results_without_lookups(self, controller):
        vision = vision_returning(VISION_BOX, None)
        steps = generated([step(PHRASE, 1), step(WEAK, 2), step(ABSENT, 3)], vision=vision)
        controller.locator = Mock()
        teach(controller, steps)

        controller.next_step()
        assert drawn(controller) == [widget_box(controller, VISION_BOX)]
        controller.next_step()
        assert drawn(controller) == []
        controller.prev_step()
        assert drawn(controller) == [widget_box(controller, VISION_BOX)]
        controller.prev_step()
        assert drawn(controller) == [widget_box(controller, PHRASE_BOX)]

        controller.locator.locate.assert_not_called()
        assert vision.call_count == 2

    def test_walking_the_lesson_does_not_spend_the_fallback_budget_again(self, controller):
        vision = Mock(return_value=Mock(box=VISION_BOX))
        count = MAX_VISION_FALLBACKS + 2
        teach(controller, generated([step(ABSENT, i) for i in range(1, count + 1)], vision=vision))

        for _ in range(count - 1):
            controller.next_step()
        # The steps past the cap were left unresolved at generation time and
        # stay that way: navigation is not a second chance to call the model.
        assert drawn(controller) == []
        for _ in range(count - 1):
            controller.prev_step()
        assert drawn(controller) == [widget_box(controller, VISION_BOX)]

        assert vision.call_count == MAX_VISION_FALLBACKS


class TestStepsWithoutAStoredLocation:
    """Steps that never went through the engine, e.g. offline demo lessons."""

    def test_missing_key_falls_back_to_a_trusted_lookup(self, controller):
        teach(controller, [step(PHRASE)])

        assert drawn(controller) == [widget_box(controller, PHRASE_BOX)]

    def test_missing_key_still_refuses_a_word_level_match(self, controller):
        teach(controller, [step(WEAK)])

        assert drawn(controller) == []

    def test_explicit_none_is_final_even_when_the_anchor_is_on_screen(self, controller):
        # The absent key means "nobody decided yet"; a stored None means the
        # routing decided against it. Rendering must not conflate the two.
        declined = step(PHRASE)
        declined[STEP_LOCATION_KEY] = None

        teach(controller, [declined])

        assert drawn(controller) == []

    def test_offline_demo_steps_use_the_same_trust_boundary(self, controller):
        controller._on_demo_step_changed(SCREEN, step(WEAK))
        assert drawn(controller) == []

        controller._on_demo_step_changed(SCREEN, step(PHRASE))
        assert drawn(controller) == [widget_box(controller, PHRASE_BOX)]
