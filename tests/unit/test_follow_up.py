"""Typed follow-ups about the displayed step: no capture, no OCR, one call.

Runs a real DesktopController offscreen with fake lesson and follow-up
workers, fake capture and the fake voice from test_narration.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMessageBox

import src.desktop.controller as controller_module
import src.input.hotkeys as hotkeys_module
from src.desktop.companion import DEFAULT_QUESTION, FloatingCompanion
from src.desktop.controller import DesktopController
from src.desktop.followup import (
    MAX_EXPLANATION_CHARS,
    MAX_HISTORY_EXCHANGES,
    MAX_QUESTION_CHARS,
    MAX_TITLE_CHARS,
    FollowUpRequest,
    answer_follow_up,
    build_follow_up_prompt,
    build_follow_up_request,
)
from src.desktop.narration import NarrationService
from src.desktop.session import Exchange
from src.input.events import InputAction
from src.input.state_machine import TutorState
from src.locator import STEP_LOCATION_KEY
from tests.unit.test_escape import FakeKeyboard
from tests.unit.test_lesson_cancellation import OCR, STEPS, FakeWorker, image, pump
from tests.unit.test_narration import FakeFactory

TWO_STEPS = STEPS + [
    {
        "step": 2,
        "title": "t2",
        "anchor": "NONE",
        "attention": "none",
        "explanation": "e2",
        STEP_LOCATION_KEY: None,
    }
]


class FakeFollowUpWorker(QObject):
    answer_ready = pyqtSignal(str)
    error = pyqtSignal(str)
    finished = pyqtSignal()
    created: list[FakeFollowUpWorker] = []

    def __init__(self, request: FollowUpRequest) -> None:
        super().__init__()
        self.request = request
        self.started = False
        self.interrupted = False
        self._running = False
        FakeFollowUpWorker.created.append(self)

    def start(self) -> None:
        self.started = self._running = True

    def requestInterruption(self) -> None:
        self.interrupted = True

    def wait(self, *args) -> bool:
        assert not self._running
        return True

    def finish(self) -> None:
        self._running = False
        self.finished.emit()


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def factory():
    return FakeFactory()


@pytest.fixture
def controller(qt_app, factory, monkeypatch):
    FakeWorker.created = []
    FakeFollowUpWorker.created = []
    monkeypatch.setattr(hotkeys_module, "keyboard", FakeKeyboard())
    monkeypatch.setattr(controller_module, "LessonWorker", FakeWorker)
    monkeypatch.setattr(controller_module, "FollowUpWorker", FakeFollowUpWorker)
    monkeypatch.setattr(controller_module, "COMPOSITOR_SETTLE_SECONDS", 0)
    monkeypatch.setattr(
        controller_module, "extract_ocr_data", Mock(side_effect=AssertionError("OCR ran"))
    )
    c = DesktopController(narration=NarrationService(factory=factory))
    c.captures = []

    def fake_capture(region=None):
        c.captures.append(region)
        return image()

    monkeypatch.setattr(c.capture_engine, "capture", fake_capture)
    monkeypatch.setattr(c.capture_engine, "supports_regions", lambda: True)
    monkeypatch.setattr(c.locator, "locate", Mock(side_effect=AssertionError("locator ran")))
    monkeypatch.setattr(c, "_show_error", Mock(side_effect=AssertionError("unexpected dialog")))
    monkeypatch.setattr(
        QMessageBox, "question", Mock(side_effect=AssertionError("unexpected dialog"))
    )
    c.companion.show()
    yield c
    c.narration.shutdown()
    c.overlay.animation_engine.stop()
    c.companion.close()
    c.ui.close()
    c.overlay.close()


def teach(c, question="what is x", steps=TWO_STEPS):
    before = len(FakeWorker.created)
    c.ask(question)
    worker = FakeWorker.created[before]
    worker.lesson_ready.emit(OCR, steps, "lesson answer")
    worker.finish()
    pump()
    return worker


def submit(c, text):
    c.companion.question_input.setText(text)
    c.companion.submit_question()


def follow_up(c, text="why?"):
    before = len(FakeFollowUpWorker.created)
    submit(c, text)
    return FakeFollowUpWorker.created[before] if len(FakeFollowUpWorker.created) > before else None


def drawn(c):
    return [(s.x, s.y, s.width, s.height) for s in c.overlay.animation_engine.shapes]


def state(c):
    return c.input_manager.current_state


def act(c, action):
    c.input_manager.handle_action(action)


# ------------------------------------------------------------ routing


class TestRouting:
    def test_idle_submit_still_captures_a_lesson(self, controller):
        submit(controller, "what is x")
        assert len(controller.captures) == 1 and len(FakeWorker.created) == 1
        assert FakeFollowUpWorker.created == [] and state(controller) is TutorState.ANALYZING

    def test_lesson_submit_is_a_follow_up_without_capture_or_ocr(self, controller):
        teach(controller)
        captures = list(controller.captures)
        worker = follow_up(controller, "  why?  ")
        assert worker is not None and worker.started
        assert worker.request.question == "why?"
        assert controller.captures == captures and len(FakeWorker.created) == 1
        assert state(controller) is TutorState.ANSWERING
        assert controller.lesson_steps == TWO_STEPS and len(drawn(controller)) == 1

    def test_blank_follow_up_starts_nothing(self, controller):
        teach(controller)
        assert follow_up(controller, "   ") is None
        assert state(controller) is TutorState.TEACHING and controller.worker is None

    @pytest.mark.parametrize("typed, expected", [("new q", "new q"), ("", DEFAULT_QUESTION)])
    def test_ask_screen_recaptures_with_typed_or_default(self, controller, typed, expected):
        first = teach(controller)
        assert first is not None
        controller.companion.question_input.setText(typed)
        controller.companion.btn_ask_screen.click()
        assert len(controller.captures) == 2
        assert FakeWorker.created[-1].question == expected
        assert FakeFollowUpWorker.created == []
        assert state(controller) is TutorState.ANALYZING and controller.lesson_steps == []


# ------------------------------------------------------------ the engine seam


class TestEngine:
    def _request(self, **overrides):
        base = dict(
            serial=1,
            session_id=1,
            lesson_id=1,
            step_index=0,
            question="why?",
            image=image(),
            ocr_data=OCR,
            capture_note="",
            step=TWO_STEPS[0],
            lesson_question="what is x",
            history=(Exchange("q1", "a1"),),
        )
        base.update(overrides)
        return build_follow_up_request(**base)

    def test_exactly_one_call_with_prompt_and_stored_image_returns_plain_text(self, monkeypatch):
        import src.desktop.followup as followup_module
        import src.lesson_engine as engine_module

        monkeypatch.setattr(
            followup_module, "visible_text_from_ocr", followup_module.visible_text_from_ocr
        )
        monkeypatch.setattr(
            engine_module, "parse_lesson_steps", Mock(side_effect=AssertionError("parsed"))
        )
        monkeypatch.setattr(
            engine_module, "locate_trusted", Mock(side_effect=AssertionError("located"))
        )
        request = self._request()
        generate = Mock(return_value=object())
        extract = Mock(return_value="  An answer.  ")

        answer = answer_follow_up(request, generate=generate, extract=extract)

        assert answer == "An answer."
        assert generate.call_count == 1
        parts = generate.call_args.args[0]
        assert len(parts) == 2 and parts[1] is request.image
        assert "why?" in parts[0] and "x" in parts[0] and "user: q1" in parts[0]

    def test_fields_are_clipped_and_snapshotted(self):
        step = {"title": "T" * 999, "explanation": "E" * 9999, "anchor": None, "context": 5}
        history = tuple(Exchange(f"q{i}", f"a{i}") for i in range(MAX_HISTORY_EXCHANGES + 3))
        request = self._request(step=step, history=history, question="w" * 9999)

        assert len(request.step_title) == MAX_TITLE_CHARS
        assert len(request.step_explanation) == MAX_EXPLANATION_CHARS
        assert len(request.question) == MAX_QUESTION_CHARS
        assert request.step_anchor == "" and request.step_context == "5"
        assert len(request.history) == MAX_HISTORY_EXCHANGES
        assert request.history[0].question == "q3"
        step["title"] = "changed"
        assert request.step_title == "T" * MAX_TITLE_CHARS
        with pytest.raises((AttributeError, TypeError)):
            request.question = "x"  # type: ignore[misc]

    def test_crop_note_and_missing_text_reach_the_prompt(self):
        prompt = build_follow_up_prompt(self._request(capture_note="cropped", ocr_data=None))
        assert "cropped" in prompt and "none could be read" in prompt
        assert "not visible" in prompt and "answer text only" in prompt

    def test_visible_text_block_is_bounded_including_separators(self):
        from src.desktop.followup import visible_text_from_ocr

        ocr = {
            "text": ["aaaa", "bbbb", "cccc"],
            "block_num": [1, 1, 1],
            "par_num": [1, 1, 1],
            "line_num": [1, 2, 3],
            "word_num": [1, 1, 1],
            "left": [0, 0, 0],
            "top": [0, 20, 40],
            "width": [10, 10, 10],
            "height": [5, 5, 5],
            "conf": [90, 90, 90],
        }
        assert visible_text_from_ocr(ocr) == "aaaa\nbbbb\ncccc"
        assert visible_text_from_ocr(ocr, max_chars=9) == "aaaa\nbbbb"
        assert visible_text_from_ocr(ocr, max_chars=8) == "aaaa"
        assert visible_text_from_ocr(ocr, max_chars=2) == "aa"
        assert visible_text_from_ocr(ocr, max_lines=1) == "aaaa"
        assert visible_text_from_ocr(ocr, max_lines=0) == ""
        assert visible_text_from_ocr(None) == ""

    def test_malformed_step_is_tolerated(self):
        request = self._request(step="not a dict")
        assert request.step_title == "" and request.step_explanation == ""


# ------------------------------------------------------------ acceptance


class TestAccepted:
    def test_success_keeps_step_and_highlight_shows_answer_records_and_speaks(
        self, controller, factory
    ):
        teach(controller)
        before = drawn(controller)
        worker = follow_up(controller)
        assert factory.engines[0].stopped == 1, "narration stopped on start"

        worker.answer_ready.emit("Because it counts.")
        worker.finish()
        pump()

        assert state(controller) is TutorState.TEACHING
        assert controller.current_step_index == 0 and drawn(controller) == before
        assert controller.lesson_steps == TWO_STEPS and controller.current_image is not None
        assert "why?" in controller.companion.lbl_title.text()
        assert controller.companion.lbl_body.text() == "Because it counts."
        assert controller.companion.lbl_counter.text() == "1 / 2"
        assert controller.session.exchanges[-1] == Exchange("why?", "Because it counts.")
        assert len(controller.session.exchanges) == 2
        assert factory.engines[-1].spoken == ["Because it counts."]
        assert controller.ui.btn_capture.isEnabled()

    def test_voice_off_shows_but_does_not_speak(self, controller, factory):
        teach(controller)
        controller.companion.chk_voice.click()
        worker = follow_up(controller)
        worker.answer_ready.emit("A")
        pump()
        assert controller.companion.lbl_body.text() == "A"
        assert len(factory.engines) == 1

    def test_replay_reads_the_answer_and_navigation_returns_to_base_steps(
        self, controller, factory
    ):
        teach(controller)
        worker = follow_up(controller)
        worker.answer_ready.emit("A")
        pump()
        controller.companion.btn_replay.click()
        pump()
        assert factory.engines[-1].spoken == ["A"]

        act(controller, InputAction.NEXT_STEP)
        pump()
        assert controller.companion.lbl_title.text() == "t2"
        assert factory.engines[-1].spoken == ["e2"]
        act(controller, InputAction.PREV_STEP)
        pump()
        assert controller.companion.lbl_title.text() == "t"
        assert controller.companion.lbl_body.text() == "e"
        assert factory.engines[-1].spoken == ["e"]
        assert len(FakeFollowUpWorker.created) == 1

    def test_next_exchange_carries_the_follow_up(self, controller):
        teach(controller)
        worker = follow_up(controller)
        worker.answer_ready.emit("A")
        worker.finish()
        pump()
        second = follow_up(controller, "and?")
        assert second.request.history[-1] == Exchange("why?", "A")


# ------------------------------------------------------------ failure and cancel


class TestRestore:
    def test_escape_restores_the_step_without_waiting_or_recording(self, controller, factory):
        teach(controller)
        before = drawn(controller)
        worker = follow_up(controller)
        act(controller, InputAction.ESCAPE)

        assert worker.interrupted and controller.worker is worker
        assert state(controller) is TutorState.TEACHING and drawn(controller) == before
        assert controller.companion.lbl_title.text() == "t"
        assert len(controller.session.exchanges) == 1
        assert len(factory.engines) == 1, "restoring does not read"
        worker.answer_ready.emit("late")
        worker.error.emit("late boom")
        worker.finish()
        pump()
        assert controller.companion.lbl_body.text() == "e"
        assert len(controller.session.exchanges) == 1 and controller.worker is None
        assert controller.lesson_steps == TWO_STEPS

    def test_error_shows_an_inline_notice_and_no_modal(self, controller):
        teach(controller)
        before = drawn(controller)
        worker = follow_up(controller)
        worker.error.emit("boom")
        pump()

        assert state(controller) is TutorState.TEACHING and drawn(controller) == before
        assert controller.companion.lbl_notice.isVisibleTo(controller.companion)
        assert len(controller.session.exchanges) == 1
        assert controller.lesson_steps == TWO_STEPS

        act(controller, InputAction.NEXT_STEP)
        assert not controller.companion.lbl_notice.isVisibleTo(controller.companion)


class TestStale:
    def test_stale_after_new_session_recapture_and_navigation_do_nothing(self, controller):
        teach(controller)
        worker = follow_up(controller)
        controller.start_new_session()
        worker.answer_ready.emit("late")
        worker.error.emit("late")
        pump()
        assert controller.session.is_empty and state(controller) is TutorState.IDLE

        # Recapture: Escape restores the step, then Ask screen replaces the
        # lesson while the follow-up worker is still draining.
        worker.finish()
        pump()
        teach(controller)
        second = follow_up(controller)
        act(controller, InputAction.ESCAPE)
        assert state(controller) is TutorState.TEACHING and second.interrupted
        controller.ask("again")
        assert controller.lesson_steps == [] and state(controller) is TutorState.ANALYZING
        second.answer_ready.emit("late")
        second.error.emit("late boom")
        pump()
        assert controller.companion.lbl_body.text() != "late"
        assert len(controller.session.exchanges) == 1
        assert state(controller) is TutorState.ANALYZING

        # Navigation: a follow-up about step 1 whose answer lands on step 2.
        second.finish()
        pump()
        FakeWorker.created[-1].lesson_ready.emit(OCR, TWO_STEPS, "a3")
        FakeWorker.created[-1].finish()
        pump()
        third = follow_up(controller)
        act(controller, InputAction.ESCAPE)
        act(controller, InputAction.NEXT_STEP)
        third.answer_ready.emit("late")
        pump()
        assert controller.companion.lbl_title.text() == "t2"
        assert controller.session.exchanges[-1].answer == "a3"

    def test_builder_failure_restores_the_step_and_starts_nothing(self, controller, monkeypatch):
        teach(controller)
        before = drawn(controller)
        monkeypatch.setattr(
            controller_module, "build_follow_up_request", Mock(side_effect=ValueError("bad"))
        )
        assert follow_up(controller) is None
        assert state(controller) is TutorState.TEACHING and drawn(controller) == before
        assert controller.companion.lbl_notice.isVisibleTo(controller.companion)
        assert controller._active_request is None and controller.worker is None
        assert len(controller.session.exchanges) == 1

    def test_stale_finished_cannot_release_the_newer_worker_or_start_old_pending(self, controller):
        teach(controller)
        first = follow_up(controller, "one")
        act(controller, InputAction.ESCAPE)
        assert follow_up(controller, "two") is None, "queued behind the draining worker"
        old_pending = controller._pending_lesson
        act(controller, InputAction.ESCAPE)
        assert controller._pending_lesson is None
        assert follow_up(controller, "three") is None
        first.finish()
        pump()
        newest = FakeFollowUpWorker.created[-1]
        assert newest.request.question == "three" and controller.worker is newest
        assert state(controller) is TutorState.ANSWERING

        controller._pending_lesson = old_pending
        controller._on_worker_finished(first)
        assert controller.worker is newest and len(FakeFollowUpWorker.created) == 2
        assert not controller.ui.btn_capture.isEnabled()
        controller._pending_lesson = None

    def test_follow_up_then_lesson_then_follow_up_keeps_one_worker(self, controller):
        teach(controller)
        first = follow_up(controller, "one")
        act(controller, InputAction.ESCAPE)
        controller.ask("fresh lesson")
        assert first.interrupted and len(FakeWorker.created) == 1
        pending = controller._pending_lesson
        assert pending is not None and pending.question == "fresh lesson"
        first.finish()
        pump()
        lesson = FakeWorker.created[-1]
        assert lesson.started and controller.worker is lesson
        lesson.lesson_ready.emit(OCR, TWO_STEPS, "a2")
        pump()
        assert follow_up(controller, "two") is None, "lesson worker still draining"
        lesson.finish()
        pump()
        assert controller.worker is FakeFollowUpWorker.created[-1]
        assert FakeFollowUpWorker.created[-1].request.lesson_id == controller._displayed_lesson

    def test_busy_state_blocks_ask_navigation_and_debug(self, controller):
        teach(controller)
        follow_up(controller)
        controller.ask("q")
        act(controller, InputAction.NEXT_STEP)
        act(controller, InputAction.TOGGLE_DEBUG)
        act(controller, InputAction.SELECT_REGION)
        assert len(FakeWorker.created) == 1 and controller.current_step_index == 0
        assert not controller.is_debug_mode and controller._selection is None
        assert state(controller) is TutorState.ANSWERING


# ------------------------------------------------------------ companion


class TestCompanion:
    @pytest.fixture
    def companion(self, qt_app):
        return FloatingCompanion()

    def test_idle_labels(self, companion):
        assert companion.btn_ask.text() == "Ask"
        assert not companion.btn_ask_screen.isVisibleTo(companion)
        assert companion.question_input.placeholderText() != "Ask about this step"

    @pytest.mark.parametrize("st", [TutorState.TEACHING, TutorState.FINISHED])
    def test_lesson_labels_and_signals(self, companion, st):
        seen = []
        companion.question_submitted.connect(lambda q: seen.append(("screen", q)))
        companion.follow_up_submitted.connect(lambda q: seen.append(("follow", q)))
        companion.apply_state(st)
        assert companion.btn_ask.text() == "Follow up"
        assert companion.question_input.placeholderText() == "Ask about this step"
        assert companion.btn_ask_screen.isVisibleTo(companion)
        assert companion.btn_select_area.isVisibleTo(companion)
        assert companion.width() == 380
        # The field keeps most of the row: only Follow up shares it.
        assert companion.question_input.width() >= 200
        companion.submit_question()
        companion.question_input.setText(" why ")
        companion.btn_ask.click()
        companion.btn_ask_screen.click()
        assert seen == [("follow", "why"), ("screen", DEFAULT_QUESTION)]

    @pytest.mark.parametrize("st", [TutorState.IDLE, TutorState.TEACHING])
    def test_primary_button_never_absorbs_the_fields_width(self, companion, st):
        from PyQt6.QtWidgets import QSizePolicy

        companion.apply_state(st)
        companion.show()
        companion.adjustSize()
        companion.ask_widget.layout().activate()

        policy = companion.btn_ask.sizePolicy().horizontalPolicy()
        assert policy is QSizePolicy.Policy.Fixed
        assert companion.btn_ask.width() <= companion.btn_ask.sizeHint().width() + 2
        text_width = companion.btn_ask.fontMetrics().horizontalAdvance(companion.btn_ask.text())
        # The compact primary action reserves the row for the learner's input.
        assert companion.btn_ask.sizeHint().width() <= text_width + 10
        assert companion.question_input.width() >= 200
        companion.close()

    def test_answering_is_busy(self, companion):
        companion.apply_state(TutorState.ANSWERING)
        assert companion.lbl_status.text() == "ANSWERING"
        assert not companion.ask_widget.isVisibleTo(companion)
        assert not companion.screen_widget.isVisibleTo(companion)
        assert companion.btn_new_session.isVisibleTo(companion)
        assert companion._thinking_timer.isActive()
