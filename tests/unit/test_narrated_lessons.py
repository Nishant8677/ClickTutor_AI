"""A real DesktopController reads accepted steps aloud through a fake voice.

Speech runs beside the displayed lesson: it starts after the first step is
rendered, stops before a step change, never advances anything, and every
cancellation path stops it. Workers are fakes, capture is a fake, and the
narration service is real but built on the fake engine from test_narration.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from PIL import Image
from PyQt6.QtWidgets import QApplication, QMessageBox

import src.desktop.controller as controller_module
import src.input.hotkeys as hotkeys_module
from src.desktop.controller import DesktopController
from src.desktop.narration import NarrationService
from src.input.events import InputAction
from src.input.state_machine import TutorState
from src.locator import STEP_LOCATION_KEY
from tests.unit.test_escape import FakeKeyboard
from tests.unit.test_lesson_cancellation import OCR, FakeWorker, pump
from tests.unit.test_narration import FakeFactory

LONG = "word " * 200  # far beyond the companion's truncation limit
STEPS = [
    {
        "step": 1,
        "title": "first",
        "anchor": "x",
        "attention": "rectangle",
        "explanation": LONG.strip(),
        STEP_LOCATION_KEY: {
            "box": {"left": 10, "top": 10, "width": 20, "height": 5},
            "source": "ocr",
            "confidence": 1.0,
        },
    },
    {
        "step": 2,
        "title": "second",
        "anchor": "NONE",
        "attention": "none",
        "explanation": "second explanation",
        STEP_LOCATION_KEY: None,
    },
]


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def factory():
    return FakeFactory()


@pytest.fixture
def controller(qt_app, factory, monkeypatch):
    FakeWorker.created = []
    monkeypatch.setattr(hotkeys_module, "keyboard", FakeKeyboard())
    monkeypatch.setattr(controller_module, "LessonWorker", FakeWorker)
    monkeypatch.setattr(controller_module, "COMPOSITOR_SETTLE_SECONDS", 0)
    c = DesktopController(narration=NarrationService(factory=factory))
    c.captures = []

    def fake_capture(region=None):
        c.captures.append(region)
        return Image.new("RGB", (800, 600))

    monkeypatch.setattr(c.capture_engine, "capture", fake_capture)
    monkeypatch.setattr(c.capture_engine, "supports_regions", lambda: True)
    monkeypatch.setattr(c.locator, "locate", Mock(side_effect=AssertionError("locator ran")))
    monkeypatch.setattr(
        c, "_show_error", Mock(side_effect=AssertionError("unexpected error dialog"))
    )
    monkeypatch.setattr(
        QMessageBox, "question", Mock(side_effect=AssertionError("unexpected question dialog"))
    )
    c.companion.show()
    yield c
    if c._selection is not None:
        c._cancel_selection()
    c.narration.shutdown()
    c.overlay.animation_engine.stop()
    c.companion.close()
    c.ui.close()
    c.overlay.close()


def teach(controller, steps=STEPS):
    """Asks, accepts a lesson and returns its worker; the pump starts the first step's voice."""
    before = len(FakeWorker.created)
    controller.ask("q")
    worker = FakeWorker.created[before]
    worker.lesson_ready.emit(OCR, steps, "answer")
    pump()
    return worker


def drawn(c):
    return [(s.x, s.y, s.width, s.height) for s in c.overlay.animation_engine.shapes]


def state(c):
    return c.input_manager.current_state


def act(c, action):
    c.input_manager.handle_action(action)


class TestAutoplay:
    def test_accepted_lesson_speaks_the_full_first_step_after_rendering(self, controller, factory):
        seen = []
        factory.on_say = lambda: seen.append(
            (drawn(controller), controller.companion.lbl_title.text())
        )

        teach(controller)

        engine = factory.engines[0]
        assert engine.spoken == [STEPS[0]["explanation"]]
        assert len(engine.spoken[0]) > len(controller.companion.lbl_body.text())
        assert seen == [(drawn(controller), "first")] and len(seen[0][0]) == 1
        assert controller.companion.btn_stop_speech.isEnabled()

    def test_nothing_is_spoken_while_the_model_is_pending(self, controller, factory):
        controller.ask("q")
        assert factory.engines == [] and state(controller) is TutorState.ANALYZING

    def test_stale_result_is_not_spoken(self, controller, factory):
        controller.ask("q")
        worker = FakeWorker.created[0]
        act(controller, InputAction.CANCEL_LESSON)
        worker.lesson_ready.emit(OCR, STEPS, "answer")
        pump()
        assert factory.engines == [] and state(controller) is TutorState.IDLE

    def test_natural_finish_waits_on_the_same_step(self, controller, factory):
        teach(controller)
        before = drawn(controller)
        # The one grab the lesson took (a physical-region dict here, since
        # this fixture's capture supports regions); finishing adds none.
        expected_captures = list(controller.captures)
        assert len(expected_captures) == 1

        factory.engines[0].finish()
        pump()

        assert state(controller) is TutorState.TEACHING
        assert controller.current_step_index == 0
        assert drawn(controller) == before
        assert controller.companion.lbl_title.text() == "first"
        assert not controller.companion.btn_stop_speech.isEnabled()
        assert controller.companion.btn_replay.isEnabled()
        assert len(factory.engines) == 1 and controller.captures == expected_captures


class TestNavigation:
    def test_next_stops_before_the_step_changes_then_speaks_the_new_step(self, controller, factory):
        # Installed before teach(): the factory hands the spy to each engine
        # as it is created, so a later assignment would miss the first one.
        at_stop = []
        factory.on_stop = lambda: at_stop.append(
            (controller.current_step_index, controller.companion.lbl_title.text())
        )
        teach(controller)

        act(controller, InputAction.NEXT_STEP)

        assert at_stop == [(0, "first")]
        pump()
        assert factory.engines[1].spoken == ["second explanation"]
        assert controller.companion.lbl_title.text() == "second"

        act(controller, InputAction.PREV_STEP)
        assert factory.engines[1].stopped == 1 and at_stop[-1] == (1, "second")
        pump()
        assert factory.engines[2].spoken == [STEPS[0]["explanation"]]

    def test_boundary_presses_do_not_restart_speech(self, controller, factory):
        teach(controller)
        act(controller, InputAction.PREV_STEP)
        act(controller, InputAction.NEXT_STEP)
        pump()
        act(controller, InputAction.NEXT_STEP)
        pump()

        assert len(factory.engines) == 2
        assert factory.engines[1].stopped == 0 and controller.narration.is_speaking()

    def test_replay_reads_the_stored_text_again_without_any_lookup(self, controller, factory):
        teach(controller)
        expected_captures = list(controller.captures)
        assert len(expected_captures) == 1
        factory.engines[0].finish()

        controller.companion.btn_replay.click()
        pump()

        assert factory.engines[1].spoken == [STEPS[0]["explanation"]]
        assert len(FakeWorker.created) == 1 and controller.captures == expected_captures
        assert controller.current_step_index == 0 and state(controller) is TutorState.TEACHING

    def test_stop_button_keeps_step_and_highlight(self, controller, factory):
        teach(controller)
        before = drawn(controller)
        controller.companion.btn_stop_speech.click()

        assert factory.engines[0].stopped == 1
        assert drawn(controller) == before and controller.lesson_steps == STEPS
        assert not controller.companion.btn_stop_speech.isEnabled()

    def test_voice_off_stops_and_on_only_allows_replay(self, controller, factory):
        teach(controller)
        controller.companion.chk_voice.click()  # off

        assert factory.engines[0].stopped == 1
        assert not controller.narration.enabled
        assert not controller.companion.btn_replay.isEnabled()

        controller.companion.chk_voice.click()  # on
        pump()
        assert len(factory.engines) == 1, "turning on starts nothing"
        controller.companion.btn_replay.click()
        pump()
        assert factory.engines[1].spoken == [STEPS[0]["explanation"]]


class TestCancellationPaths:
    def test_selection_cancellation_restores_silently_and_replay_works(self, controller, factory):
        teach(controller)
        act(controller, InputAction.SELECT_REGION)
        assert factory.engines[0].stopped == 1
        assert controller._active_request is None

        act(controller, InputAction.ESCAPE)

        assert state(controller) is TutorState.TEACHING
        assert controller.companion.lbl_title.text() == "first"
        assert len(factory.engines) == 1, "restoring does not read again"

        controller.replay_narration()
        pump()
        assert factory.engines[1].spoken == [STEPS[0]["explanation"]]

    def test_draft_escape_keeps_speaking_but_lesson_escape_stops(self, controller, factory):
        teach(controller)
        controller.companion.question_input.setText("half a quest")

        act(controller, InputAction.ESCAPE)
        assert controller.narration.is_speaking() and controller.lesson_steps == STEPS

        act(controller, InputAction.ESCAPE)
        assert factory.engines[0].stopped == 1
        assert controller.lesson_steps == [] and state(controller) is TutorState.IDLE

    def test_new_ask_stops_before_the_capture(self, controller, factory):
        at_stop = []
        factory.on_stop = lambda: at_stop.append(len(controller.captures))
        teach(controller)  # the first ask has nothing to stop yet

        controller.ask("again")

        assert at_stop == [1], "stopped before the second grab"
        assert state(controller) is TutorState.ANALYZING

    def test_stale_worker_cannot_speak_over_the_newer_lesson(self, controller, factory):
        first = teach(controller)
        controller.ask("again")
        # The second request waits behind the cancelled first worker; its own
        # worker only exists once first has reported finished.
        assert len(FakeWorker.created) == 1
        first.lesson_ready.emit(OCR, STEPS, "late duplicate")
        first.finish()
        pump()
        assert len(factory.engines) == 1, "the retired result is dropped unspoken"
        second = FakeWorker.created[-1]
        assert second is not first and second.started

        second.lesson_ready.emit(OCR, STEPS[1:], "answer")
        pump()

        assert factory.engines[1].spoken == ["second explanation"]
        assert controller.narration.current_token.lesson_id == 2
        factory.engines[0].fail("late failure")
        assert controller.companion.lbl_voice_notice.text() == ""


class TestStaleCallbacks:
    """Only the utterance the controller last asked for may touch the voice row."""

    def test_failure_from_an_earlier_replay_of_the_same_step_is_ignored(self, controller, factory):
        teach(controller)
        controller.companion.btn_replay.click()
        pump()
        old, new = factory.engines
        assert old.stopped == 1 and new.spoken == [STEPS[0]["explanation"]]

        old.fail("late failure")
        old.finish()

        assert controller.companion.lbl_voice_notice.text() == ""
        assert controller.narration.is_speaking()
        assert controller.companion.btn_stop_speech.isEnabled()

    def test_failure_from_an_earlier_step_is_ignored(self, controller, factory):
        teach(controller)
        act(controller, InputAction.NEXT_STEP)
        pump()
        assert factory.engines[1].spoken == ["second explanation"]

        factory.engines[0].fail("late failure")

        assert controller.companion.lbl_voice_notice.text() == ""
        assert controller.companion.lbl_title.text() == "second"
        assert controller.companion.btn_stop_speech.isEnabled()

    @pytest.mark.parametrize(
        "end",
        [
            lambda c: c.companion.btn_stop_speech.click(),
            lambda c: c.companion.chk_voice.click(),
            lambda c: c.narration.shutdown(),
        ],
        ids=["stop", "voice-off", "shutdown"],
    )
    def test_failure_after_the_utterance_was_ended_is_ignored(self, controller, factory, end):
        teach(controller)
        engine = factory.engines[0]

        end(controller)
        assert engine.stopped == 1
        engine.fail("late failure")
        engine.finish()

        assert controller.companion.lbl_voice_notice.text() == ""
        assert not controller.narration.is_speaking()
        assert controller.companion.lbl_title.text() == "first"

    def test_debug_entry_stops_and_restoration_is_silent(self, controller, factory):
        teach(controller)
        act(controller, InputAction.TOGGLE_DEBUG)
        assert factory.engines[0].stopped == 1

        act(controller, InputAction.TOGGLE_DEBUG)

        assert controller.companion.lbl_title.text() == "first"
        assert len(drawn(controller)) == 1
        assert len(factory.engines) == 1

    def test_shutdown_stops_and_leaves_the_text_lesson(self, controller, factory):
        teach(controller)
        controller.narration.shutdown()

        assert factory.engines[0].stopped == 1
        assert controller.lesson_steps == STEPS and state(controller) is TutorState.TEACHING
        controller.replay_narration()
        act(controller, InputAction.NEXT_STEP)
        pump()
        assert len(factory.engines) == 1
        assert controller.companion.lbl_title.text() == "second"


class TestTextOnly:
    def test_unavailable_voice_is_indicated_and_the_lesson_still_works(self, qt_app, monkeypatch):
        monkeypatch.setattr(hotkeys_module, "keyboard", FakeKeyboard())
        monkeypatch.setattr(controller_module, "LessonWorker", FakeWorker)
        factory = FakeFactory(available=False)
        c = DesktopController(narration=NarrationService(factory=factory))
        monkeypatch.setattr(
            c.capture_engine, "capture", lambda region=None: Image.new("RGB", (8, 8))
        )
        try:
            assert c.companion.chk_voice.text() == "Voice unavailable"
            assert not c.companion.chk_voice.isEnabled()
            assert c.companion.voice_widget.isVisibleTo(c.companion)
            teach(c)
            assert state(c) is TutorState.TEACHING and factory.engines == []
            assert not c.companion.btn_replay.isVisibleTo(c.companion)
            act(c, InputAction.NEXT_STEP)
            assert c.companion.lbl_title.text() == "second"
        finally:
            c.overlay.animation_engine.stop()
            c.companion.close()
            c.ui.close()
            c.overlay.close()

    def test_engine_failure_leaves_navigation_usable(self, controller, factory):
        factory.initial = "error"
        teach(controller)

        assert state(controller) is TutorState.TEACHING
        assert controller.companion.lbl_voice_notice.text() == "Voice failed"
        assert not controller.narration.is_speaking()
        act(controller, InputAction.NEXT_STEP)
        pump()
        assert controller.companion.lbl_title.text() == "second"

    def test_failure_notice_survives_refresh_and_clears_on_a_working_read(
        self, controller, factory
    ):
        factory.initial = "error"
        teach(controller)
        assert controller.companion.lbl_voice_notice.text() == "Voice failed"

        controller.stop_narration()  # a routine refresh keeps the note
        assert controller.companion.lbl_voice_notice.text() == "Voice failed"

        factory.initial = "ready"
        controller.companion.btn_replay.click()
        pump()
        assert factory.engines[-1].spoken == [STEPS[0]["explanation"]]
        assert controller.companion.lbl_voice_notice.text() == ""

    def test_failure_notice_clears_when_a_new_lesson_replaces_it(self, controller, factory):
        factory.initial = "error"
        first = teach(controller)
        assert controller.companion.lbl_voice_notice.text() == "Voice failed"
        first.finish()  # a new ask would otherwise queue behind this worker
        pump()

        controller.companion.chk_voice.click()  # off: the next lesson is unread
        assert controller.companion.lbl_voice_notice.text() == "Voice failed"
        teach(controller)

        assert controller.companion.lbl_title.text() == "first"
        assert controller.companion.lbl_voice_notice.text() == ""

    def test_voice_switch_is_reachable_before_asking(self, controller):
        assert state(controller) is TutorState.IDLE
        assert controller.companion.voice_widget.isVisibleTo(controller.companion)
        assert (
            controller.companion.chk_voice.isEnabled()
            and controller.companion.chk_voice.isChecked()
        )
        assert not controller.companion.btn_replay.isVisibleTo(controller.companion)
