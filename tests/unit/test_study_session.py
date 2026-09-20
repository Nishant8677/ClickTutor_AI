"""One in-memory study session per launch, remembered until New session.

Every accepted lesson becomes context for the next question; nothing else
does. Each request carries a frozen snapshot of that context and the id of
the session it was asked in, so a result from a session the learner has ended
is dropped before it can draw, speak, open a dialog or be remembered.

Runs a real DesktopController on the offscreen platform with the fake worker,
capture and voice used by the cancellation and narration suites.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

import src.desktop.controller as controller_module
import src.input.hotkeys as hotkeys_module
import src.lesson_engine as engine_module
from src.attention.coordinates import CaptureGeometry
from src.desktop.companion import FloatingCompanion
from src.desktop.controller import DesktopController, LessonRequest, LessonWorker
from src.desktop.narration import NarrationService
from src.desktop.selection import SelectedRegion
from src.desktop.session import (
    MAX_EXCHANGES,
    MAX_HISTORY_CHARS,
    Exchange,
    StudySession,
    bound_exchanges,
)
from src.input.events import InputAction
from src.input.state_machine import TutorState
from tests.unit.test_escape import FakeKeyboard
from tests.unit.test_lesson_cancellation import OCR, STEPS, FakeWorker, image, pump
from tests.unit.test_narration import FakeFactory


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
    monkeypatch.setattr(c.capture_engine, "capture", lambda region=None: image())
    monkeypatch.setattr(c.capture_engine, "supports_regions", lambda: True)
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


def ask(controller, question="q") -> FakeWorker | None:
    before = len(FakeWorker.created)
    controller.ask(question)
    return FakeWorker.created[before] if len(FakeWorker.created) > before else None


def accept(controller, question, answer, steps=STEPS) -> FakeWorker:
    """Asks, has the worker succeed and finish, and returns it."""
    worker = ask(controller, question)
    assert worker is not None
    worker.lesson_ready.emit(OCR, steps, answer)
    worker.finish()
    pump()
    return worker


def state(c) -> TutorState:
    return c.input_manager.current_state


def history_of(worker) -> list[dict[str, str]]:
    return worker.request.history_messages()


def pair(question, answer) -> list[dict[str, str]]:
    return [
        {"role": "user", "content": question},
        {"role": "assistant", "content": answer},
    ]


# ------------------------------------------------------------ the pure session


class TestStudySession:
    def test_starts_empty(self):
        session = StudySession(1)

        assert session.is_empty
        assert session.exchanges == ()
        assert session.snapshot() == ()

    def test_records_in_order(self):
        session = StudySession(1)
        session.record("q1", "a1")
        session.record("q2", "a2")

        assert session.exchanges == (Exchange("q1", "a1"), Exchange("q2", "a2"))

    def test_blank_question_is_not_recorded(self):
        session = StudySession(1)
        session.record("   ", "answer")

        assert session.is_empty

    def test_snapshot_is_immutable_and_unaffected_by_later_records(self):
        session = StudySession(1)
        session.record("q1", "a1")
        snapshot = session.snapshot()
        session.record("q2", "a2")

        assert snapshot == (Exchange("q1", "a1"),)
        with pytest.raises((AttributeError, TypeError)):
            snapshot[0].answer = "tampered"  # type: ignore[misc]
        with pytest.raises(TypeError):
            snapshot[0] = Exchange("x", "y")  # type: ignore[index]

    def test_messages_are_fresh_copies(self):
        exchange = Exchange("q", "a")
        first = exchange.messages()
        first[0]["content"] = "tampered"

        assert exchange.messages() == pair("q", "a")


class TestBounds:
    def test_exchange_count_keeps_the_newest(self):
        exchanges = [Exchange(f"q{i}", f"a{i}") for i in range(MAX_EXCHANGES + 3)]

        kept = bound_exchanges(exchanges)

        assert len(kept) == MAX_EXCHANGES
        assert kept[0] == exchanges[3] and kept[-1] == exchanges[-1]

    def test_character_budget_evicts_whole_oldest_exchanges_first(self):
        exchanges = [Exchange("q", "a" * 5_000) for _ in range(4)]  # 5,001 each

        kept = bound_exchanges(exchanges)

        assert kept == (exchanges[2], exchanges[3])
        assert sum(e.chars() for e in kept) <= MAX_HISTORY_CHARS

    def test_oversized_newest_exchange_keeps_its_question_and_clips_the_answer(self):
        old = Exchange("old", "context")
        newest = Exchange("q" * 100, "a" * (MAX_HISTORY_CHARS + 500))

        kept = bound_exchanges([old, newest])

        assert len(kept) == 1
        assert kept[0].question == newest.question
        assert kept[0].answer == "a" * (MAX_HISTORY_CHARS - 100)
        assert kept[0].chars() == MAX_HISTORY_CHARS
        assert bound_exchanges([old, newest]) == kept, "deterministic"

    def test_bounds_hold_through_the_session(self):
        session = StudySession(1, max_exchanges=2, max_chars=20)
        session.record("q1", "a" * 8)  # 10
        session.record("q2", "b" * 8)  # 10
        session.record("q3", "c" * 8)  # 10: count bound drops q1
        assert session.exchanges == (Exchange("q2", "b" * 8), Exchange("q3", "c" * 8))

        session.record("q4", "d" * 30)  # alone over budget: q2, q3 go, answer clipped
        assert session.exchanges == (Exchange("q4", "d" * 18),)

    def test_non_positive_budgets_keep_nothing(self):
        assert bound_exchanges([Exchange("q", "a")], max_exchanges=0) == ()
        assert bound_exchanges([Exchange("q", "a")], max_chars=0) == ()


class TestRequestSnapshot:
    def test_history_travels_as_role_content_messages(self):
        request = LessonRequest(
            1, "q3", image(), session_id=7, history=(Exchange("q1", "a1"), Exchange("q2", "a2"))
        )

        assert request.history_messages() == pair("q1", "a1") + pair("q2", "a2")

    def test_default_request_has_no_history(self):
        request = LessonRequest(1, "q", image())

        assert request.history == () and request.history_messages() == []


class TestWorkerSeam:
    """The real worker hands the engine the snapshot, not an empty list."""

    def test_run_passes_fresh_history_messages_to_the_engine(self, qt_app, monkeypatch):
        history = (Exchange("q1", "a1"), Exchange("q2", "a2"))
        request = LessonRequest(1, "q3", image(), session_id=3, history=history)
        worker = LessonWorker(request)
        monkeypatch.setattr(controller_module, "extract_ocr_data", lambda _image: OCR)
        received: list[tuple] = []

        class FakeEngine:
            def __init__(self, image, ocr_data, vision_locator=None) -> None:
                assert ocr_data is OCR

            def generate_lesson(self, question, history, explanation_text):
                received.append((question, history, explanation_text))
                # Tamper with what was handed over, as a careless caller might.
                history[0]["content"] = "tampered"
                history.append({"role": "user", "content": "extra"})
                return "a3", None, STEPS

        monkeypatch.setattr(engine_module, "LessonEngine", FakeEngine)
        results: list[tuple] = []
        worker.lesson_ready.connect(lambda ocr, steps, answer: results.append((steps, answer)))
        worker.error.connect(lambda message: results.append(("error", message)))

        worker.run()

        assert len(received) == 1
        question, messages, context = received[0]
        assert question == "q3" and context == ""
        assert messages[0]["content"] == "tampered", "the engine got a plain mutable list"
        assert request.history == history
        assert request.history_messages() == pair("q1", "a1") + pair("q2", "a2")
        assert results == [(STEPS, "a3")]


# ------------------------------------------------------------ the controller


class TestSessionContext:
    def test_fresh_controller_has_an_empty_session(self, controller):
        assert controller.session.is_empty
        assert controller.session.session_id == 1

    def test_first_request_has_no_history_and_the_next_carries_the_pair(self, controller):
        first = accept(controller, "what is x", "x is a counter")
        assert history_of(first) == []
        assert first.request.session_id == controller.session.session_id

        second = ask(controller, "and y")

        assert history_of(second) == pair("what is x", "x is a counter")
        assert controller.session.exchanges == (Exchange("what is x", "x is a counter"),)

    def test_request_history_is_a_snapshot(self, controller):
        accept(controller, "q1", "a1")
        second = ask(controller, "q2")
        second.lesson_ready.emit(OCR, STEPS, "a2")
        second.finish()
        pump()
        third = ask(controller, "q3")

        assert history_of(second) == pair("q1", "a1"), "unchanged by the later answer"
        assert history_of(third) == pair("q1", "a1") + pair("q2", "a2")
        controller.start_new_session()
        assert history_of(third) == pair("q1", "a1") + pair("q2", "a2"), "unchanged by reset"
        messages = history_of(third)
        messages[0]["content"] = "tampered"
        assert history_of(third)[0]["content"] == "q1"

    def test_cancelled_result_is_not_remembered(self, controller):
        worker = ask(controller, "q")
        controller.input_manager.handle_action(InputAction.CANCEL_LESSON)
        worker.lesson_ready.emit(OCR, STEPS, "late")
        worker.finish()
        pump()

        assert controller.session.is_empty

    def test_error_is_not_remembered(self, controller, monkeypatch):
        monkeypatch.setattr(
            QMessageBox, "question", Mock(return_value=QMessageBox.StandardButton.No)
        )
        monkeypatch.setattr(controller, "_show_error", Mock())
        worker = ask(controller, "q")
        worker.error.emit("boom")
        worker.finish()
        pump()

        assert controller.session.is_empty
        assert history_of(ask(controller, "again")) == []

    def test_empty_steps_are_not_remembered(self, controller):
        accept(controller, "q", "an answer with no steps", steps=[])

        assert state(controller) is TutorState.IDLE
        assert controller.session.is_empty

    def test_stale_crop_result_is_not_remembered(self, controller, monkeypatch):
        screen = {"left": 0, "top": 0, "width": 3000, "height": 2000}
        geometry = CaptureGeometry.from_logical_rect(100, 50, 200, 200, screen, 1.0)
        controller._selected_region = SelectedRegion(
            geometry, controller._current_screen_signature()
        )
        monkeypatch.setattr(controller, "_selection_still_valid", lambda selected: True)
        worker = ask(controller, "q")
        # The screen changed between the grab and the result.
        monkeypatch.setattr(controller, "_request_matches_screen", lambda request: False)

        worker.lesson_ready.emit(OCR, STEPS, "answer")
        worker.finish()
        pump()

        assert state(controller) is TutorState.IDLE
        assert controller.session.is_empty


class TestNewSession:
    def test_clears_history_lesson_capture_selection_and_draft_but_keeps_voice(
        self, controller, factory
    ):
        controller.companion.chk_voice.click()  # voice off: the preference to preserve
        assert not controller.narration.enabled
        accept(controller, "q1", "a1")
        controller._selected_region = SelectedRegion(
            CaptureGeometry.from_logical_rect(
                0, 0, 100, 100, {"left": 0, "top": 0, "width": 800, "height": 600}, 1.0
            ),
            controller._current_screen_signature(),
        )
        controller.companion.set_selected_area("100 × 100")
        controller.companion.question_input.setText("half a quest")
        controller.companion.set_voice_controls(True, False, False, notice="Voice failed")
        assert state(controller) is TutorState.TEACHING

        controller.companion.btn_new_session.click()

        assert controller.session.is_empty and controller.session.session_id == 2
        assert state(controller) is TutorState.IDLE
        assert controller.lesson_steps == [] and controller.current_step_index == 0
        assert controller.overlay.animation_engine.shapes == []
        assert controller.overlay.bg_pixmap is None
        assert controller.current_image is None and controller.ocr_data is None
        assert controller.image_path is None and controller._displayed_geometry is None
        assert controller._selected_region is None
        assert not controller.companion.has_selected_area()
        assert controller.companion.question_input.text() == ""
        assert not controller.companion.lbl_question.isVisibleTo(controller.companion)
        assert controller.companion.lbl_status.text() == "READY"
        assert controller.companion.lbl_voice_notice.text() == ""
        assert not controller.narration.enabled, "voice preference survives"
        assert not controller.companion.chk_voice.isChecked()
        assert controller.ui.btn_capture.isEnabled()
        assert controller._active_request is None and controller._pending_lesson is None

    def test_voice_on_is_preserved_too_and_speech_stops(self, controller, factory):
        accept(controller, "q1", "a1")
        assert controller.narration.is_speaking()

        controller.start_new_session()

        assert factory.engines[0].stopped == 1
        assert not controller.narration.is_speaking()
        assert controller.narration.enabled and controller.companion.chk_voice.isChecked()
        factory.engines[0].fail("late failure")
        assert controller.companion.lbl_voice_notice.text() == ""

    def test_retires_the_running_worker_success_and_error(self, controller, factory):
        worker = ask(controller, "q")
        old_session = controller.session.session_id

        controller.start_new_session()

        assert worker.interrupted
        assert worker.request.session_id == old_session != controller.session.session_id
        assert controller.worker is worker, "a running thread is never dropped early"
        worker.lesson_ready.emit(OCR, STEPS, "late")
        worker.error.emit("late boom")  # the fixture's dialogs raise if reached
        pump()

        assert state(controller) is TutorState.IDLE
        assert controller.lesson_steps == [] and controller.session.is_empty
        assert factory.engines == [], "nothing spoken"
        assert controller.current_image is None

    def test_old_result_is_dropped_even_if_it_still_holds_the_serial(self, controller):
        """The session id alone must be enough to reject an old result."""
        worker = ask(controller, "q")
        controller.start_new_session()
        # Reinstate the serial as if a cleared list were the only guard.
        controller._active_request = worker.request.serial

        worker.lesson_ready.emit(OCR, STEPS, "late")
        pump()

        assert controller.lesson_steps == [] and controller.session.is_empty

    def test_old_finished_cannot_release_the_new_worker_or_start_an_old_pending(self, controller):
        first = ask(controller, "first")
        controller.input_manager.handle_action(InputAction.CANCEL_LESSON)
        assert ask(controller, "old pending") is None
        old_pending = controller._pending_lesson
        assert old_pending is not None

        controller.start_new_session()
        assert controller._pending_lesson is None
        assert ask(controller, "new") is None, "queued behind the draining old worker"
        pending = controller._pending_lesson
        assert pending is not None and pending.session_id == controller.session.session_id
        assert pending.history == ()

        first.finish()
        pump()

        second = FakeWorker.created[-1]
        assert second.question == "new" and second.started
        assert controller.worker is second
        assert state(controller) is TutorState.ANALYZING

        # A late finished handler still carrying the retired worker. The real
        # object has been released by now, so the closure's argument is
        # invoked directly rather than emitted from a deleted QObject.
        controller._pending_lesson = old_pending
        controller._on_worker_finished(first)

        assert controller.worker is second
        assert not controller.ui.btn_capture.isEnabled()
        assert controller._pending_lesson is old_pending, "not consumed by the stale handler"
        assert len(FakeWorker.created) == 2, "no worker started for the old pending request"
        controller._pending_lesson = None

    def test_request_in_the_new_session_carries_no_old_context(self, controller):
        accept(controller, "q1", "a1")
        controller.start_new_session()

        worker = ask(controller, "q2")

        assert worker is not None
        assert worker.request.session_id == 2
        assert history_of(worker) == []
        worker.lesson_ready.emit(OCR, STEPS, "a2")
        pump()
        assert controller.session.exchanges == (Exchange("q2", "a2"),)
        assert state(controller) is TutorState.TEACHING

    def test_new_session_over_an_open_error_dialog_retires_it(self, controller, monkeypatch):
        started = Mock()
        monkeypatch.setattr(controller.demo_manager, "start_demo", started)
        monkeypatch.setattr(controller.demo_manager, "get_available_demos", lambda: {"d": {}})

        def reset_then_accept(*args, **kwargs):
            controller.start_new_session()
            return QMessageBox.StandardButton.Yes

        monkeypatch.setattr(QMessageBox, "question", reset_then_accept)
        worker = ask(controller, "q")
        worker.error.emit("boom")
        pump()

        started.assert_not_called()
        assert controller._modal_request is None
        assert state(controller) is TutorState.IDLE

    def test_new_session_stops_a_demo(self, controller, monkeypatch):
        def fake_demo(demo_id):
            controller.demo_manager.is_running = True
            controller.demo_manager.demo_started.emit("demo/x/screenshot.png")

        def fake_stop():
            controller.demo_manager.is_running = False
            controller.demo_manager.demo_stopped.emit()

        monkeypatch.setattr(controller.demo_manager, "start_demo", fake_demo)
        monkeypatch.setattr(controller.demo_manager, "stop_demo", fake_stop)
        controller.start_demo("x")
        assert state(controller) is TutorState.TEACHING

        controller.start_new_session()

        assert not controller.demo_manager.is_running
        assert state(controller) is TutorState.IDLE

    def test_new_session_while_idle_is_harmless(self, controller):
        controller.start_new_session()
        controller.start_new_session()

        assert controller.session.session_id == 3
        assert state(controller) is TutorState.IDLE
        assert controller.ui.btn_capture.isEnabled()


class TestCompanionControl:
    @pytest.mark.parametrize(
        "tutor_state",
        [
            TutorState.IDLE,
            TutorState.CAPTURING,
            TutorState.ANALYZING,
            TutorState.TEACHING,
            TutorState.FINISHED,
        ],
    )
    def test_control_is_reachable_and_emits_once_per_click(self, qt_app, tutor_state):
        companion = FloatingCompanion()
        seen = []
        companion.new_session_requested.connect(lambda: seen.append(True))
        companion.apply_state(tutor_state)

        assert companion.btn_new_session.isVisibleTo(companion)
        assert companion.btn_new_session.isEnabled()
        companion.btn_new_session.click()
        assert seen == [True]
        companion.btn_new_session.click()
        assert seen == [True, True]
