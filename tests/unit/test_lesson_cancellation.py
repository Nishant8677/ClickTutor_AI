"""Cancelling or replacing a lesson invalidates the work in flight for it.

The defect: cancel cleared the lesson and set IDLE while the LessonWorker kept
running. Its later success restored the cancelled lesson; its error opened a
dialog over a newer one. Asking again dropped the only reference to a thread
that was still running. Every ask now carries a serial assigned before the
capture, and nothing a worker reports for a retired serial may touch the UI.

Everything runs through a real DesktopController on the offscreen platform.
Workers are fakes driven by the test, so no OCR, capture or model runs; their
signals are connected with QueuedConnection exactly as the real worker's are,
so a result is delivered when the test pumps the event loop, which is where
the ordering cases live. One test uses a real QThread to confirm delivery
lands on the GUI thread.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from PIL import Image
from PyQt6 import sip
from PyQt6.QtCore import QEvent, QEventLoop, QObject, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMessageBox

import src.desktop.controller as controller_module
import src.lesson_engine as engine_module
from src.desktop.controller import DesktopController, LessonRequest, LessonWorker
from src.input.events import InputAction
from src.input.state_machine import TutorState
from src.locator import STEP_LOCATION_KEY

OCR = {"text": ["x"], "left": [0], "top": [0], "width": [1], "height": [1], "conf": [90]}
STEPS = [
    {
        "step": 1,
        "title": "t",
        "anchor": "x",
        "attention": "rectangle",
        "explanation": "e",
        STEP_LOCATION_KEY: {
            "box": {"left": 10, "top": 10, "width": 20, "height": 5},
            "source": "ocr",
            "confidence": 1.0,
        },
    }
]


def image(width=200, height=100):
    return Image.new("RGB", (width, height))


def pump():
    """Delivers every queued signal exactly once, in emission order."""
    QApplication.sendPostedEvents()
    QApplication.processEvents()


def flush_deferred_deletes():
    """Runs pending deleteLater() calls, as returning to the event loop would."""
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


class FakeWorker(QObject):
    """Stands in for LessonWorker; the test decides when and how it reports."""

    lesson_ready = pyqtSignal(dict, list, str)
    error = pyqtSignal(str)
    finished = pyqtSignal()

    created: list[FakeWorker] = []

    def __init__(self, request: LessonRequest) -> None:
        super().__init__()
        self.request = request
        self.image = request.image
        self.question = request.question
        self.started = False
        self.interrupted = False
        self._running = False
        FakeWorker.created.append(self)

    def start(self) -> None:
        self.started = True
        self._running = True

    def requestInterruption(self) -> None:
        self.interrupted = True

    def isRunning(self) -> bool:
        return self._running

    def wait(self, *args) -> bool:
        assert not self._running, "wait() before finished would block the GUI"
        return True

    def finish(self) -> None:
        self._running = False
        self.finished.emit()


class ThreadedFakeWorker(QThread):
    """A real thread that reports a lesson, for the delivery-thread check."""

    lesson_ready = pyqtSignal(dict, list, str)
    error = pyqtSignal(str)

    def __init__(self, request: LessonRequest) -> None:
        super().__init__()
        self.request = request
        self.image = request.image
        self.question = request.question

    def run(self) -> None:
        self.lesson_ready.emit(OCR, STEPS, "answer")


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def controller(qt_app, monkeypatch):
    FakeWorker.created = []
    monkeypatch.setattr(controller_module, "LessonWorker", FakeWorker)
    c = DesktopController()
    c.captures = []

    def fake_capture(region=None):
        c.captures.append(region)
        return image()

    monkeypatch.setattr(c.capture_engine, "capture", fake_capture)
    # No dialog may open unless a test installs its own stand-in.
    monkeypatch.setattr(
        c, "_show_error", Mock(side_effect=AssertionError("unexpected error dialog"))
    )
    monkeypatch.setattr(
        QMessageBox, "question", Mock(side_effect=AssertionError("unexpected question dialog"))
    )
    yield c
    c.overlay.animation_engine.stop()


def cancel(controller):
    controller.input_manager.handle_action(InputAction.CANCEL_LESSON)


def ask(controller, question="q") -> FakeWorker | None:
    """Asks and returns the worker started for it, or None if it was queued."""
    before = len(FakeWorker.created)
    controller.ask(question)
    return FakeWorker.created[before] if len(FakeWorker.created) > before else None


def drawn(controller):
    return [(s.x, s.y, s.width, s.height) for s in controller.overlay.animation_engine.shapes]


def state(controller) -> TutorState:
    return controller.input_manager.current_state


# ------------------------------------------------------------ normal path


class TestNormalCompletion:
    def test_success_binds_the_lesson_to_its_capture(self, controller):
        worker = ask(controller, "what is x")

        assert state(controller) is TutorState.ANALYZING
        assert controller.current_image is None, "capture stays local until accepted"

        worker.lesson_ready.emit(OCR, STEPS, "answer")
        pump()

        assert state(controller) is TutorState.TEACHING
        assert controller.lesson_steps == STEPS
        assert controller.current_image is worker.image
        assert len(drawn(controller)) == 1
        assert controller.ui.btn_capture.isEnabled()

    def test_finished_releases_the_worker_reference(self, controller):
        worker = ask(controller)
        worker.lesson_ready.emit(OCR, STEPS, "answer")
        worker.finish()
        pump()

        assert controller.worker is None
        assert state(controller) is TutorState.TEACHING
        # The connected closures held the QObject, its request and its image;
        # clearing the attribute alone left all of that alive.
        flush_deferred_deletes()
        assert sip.isdeleted(worker)

    def test_empty_lesson_returns_to_idle(self, controller):
        worker = ask(controller)
        worker.lesson_ready.emit(OCR, [], "answer")
        pump()

        assert state(controller) is TutorState.IDLE
        assert controller.ui.btn_capture.isEnabled()

    def test_error_offers_the_demo_and_reports_when_declined(self, controller, monkeypatch):
        monkeypatch.setattr(
            QMessageBox, "question", Mock(return_value=QMessageBox.StandardButton.No)
        )
        shown = Mock()
        monkeypatch.setattr(controller, "_show_error", shown)

        worker = ask(controller)
        worker.error.emit("boom")
        pump()

        assert state(controller) is TutorState.IDLE
        shown.assert_called_once_with("Error: boom")
        assert controller.ui.btn_capture.isEnabled()

    def test_error_accepted_starts_the_fallback_demo(self, controller, monkeypatch):
        monkeypatch.setattr(
            QMessageBox, "question", Mock(return_value=QMessageBox.StandardButton.Yes)
        )
        monkeypatch.setattr(controller.demo_manager, "get_available_demos", lambda: {"d": {}})
        started = Mock()
        monkeypatch.setattr(controller.demo_manager, "start_demo", started)

        worker = ask(controller)
        worker.error.emit("boom")
        pump()

        started.assert_called_once_with("d")


# ------------------------------------------------------------ cancel


class TestCancelledWorker:
    def test_stale_success_does_not_restore_the_lesson(self, controller):
        worker = ask(controller)
        cancel(controller)
        assert worker.interrupted

        worker.lesson_ready.emit(OCR, STEPS, "answer")
        pump()

        assert state(controller) is TutorState.IDLE
        assert controller.lesson_steps == []
        assert drawn(controller) == []
        assert controller.current_image is None
        assert controller.ui.lbl_status.text() == "Lesson cancelled. Ready."

    def test_stale_error_opens_no_dialog(self, controller):
        worker = ask(controller)
        cancel(controller)

        worker.error.emit("boom")  # the fixture's dialogs raise if reached
        pump()

        assert state(controller) is TutorState.IDLE

    def test_cancel_via_escape_also_retires_the_request(self, controller, monkeypatch):
        monkeypatch.setattr(type(controller.hotkeys), "escape_hooked", property(lambda self: False))
        worker = ask(controller)

        controller.on_local_escape()
        worker.lesson_ready.emit(OCR, STEPS, "answer")
        pump()

        assert state(controller) is TutorState.IDLE
        assert controller.lesson_steps == []

    def test_worker_stays_referenced_until_finished_then_is_released(self, controller):
        worker = ask(controller)
        cancel(controller)
        pump()
        flush_deferred_deletes()
        assert controller.worker is worker
        assert not sip.isdeleted(worker), "a running thread is never destroyed"

        worker.finish()
        pump()

        assert controller.worker is None
        assert controller.ui.btn_capture.isEnabled()
        flush_deferred_deletes()
        assert sip.isdeleted(worker)


# ------------------------------------------------------------ replacement


class TestReplacement:
    def test_replacement_waits_for_the_cancelled_worker(self, controller):
        first = ask(controller, "first")
        cancel(controller)

        assert ask(controller, "second") is None, "no second worker while the first runs"
        assert state(controller) is TutorState.ANALYZING
        assert "Waiting" in controller.ui.lbl_status.text()
        assert not controller.ui.btn_capture.isEnabled()

        first.lesson_ready.emit(OCR, [{"step": 9, "title": "stale", "anchor": "x"}], "old")
        first.finish()
        pump()

        second = FakeWorker.created[-1]
        assert second is not first and second.started
        assert second.question == "second"
        assert controller.worker is second
        assert controller.lesson_steps == [], "stale result was dropped"
        assert not controller.ui.btn_capture.isEnabled(), "replacement is busy"

        second.lesson_ready.emit(OCR, STEPS, "answer")
        pump()
        assert state(controller) is TutorState.TEACHING
        assert controller.current_image is second.image

    def test_stale_result_queued_before_the_replacement_is_dropped(self, controller):
        first = ask(controller, "first")
        cancel(controller)
        # The old worker reports and exits before its signals are delivered.
        first.lesson_ready.emit(OCR, STEPS, "old")
        first.finish()

        assert ask(controller, "second") is None
        pump()

        second = FakeWorker.created[-1]
        assert second.started and second.question == "second"
        assert controller.lesson_steps == []
        assert state(controller) is TutorState.ANALYZING

    def test_cancelling_the_pending_replacement_starts_nothing(self, controller):
        first = ask(controller, "first")
        cancel(controller)
        ask(controller, "second")
        cancel(controller)

        first.finish()
        pump()

        assert len(FakeWorker.created) == 1
        assert controller.worker is None
        assert state(controller) is TutorState.IDLE
        assert controller.ui.btn_capture.isEnabled()

    def test_repeated_cancel_and_ask_keeps_only_the_latest(self, controller):
        first = ask(controller, "first")
        for n in range(5):
            cancel(controller)
            assert ask(controller, f"retry {n}") is None

        first.finish()
        pump()

        assert len(FakeWorker.created) == 2
        assert FakeWorker.created[-1].question == "retry 4"
        assert controller.worker is FakeWorker.created[-1]

    def test_ask_while_teaching_retires_the_shown_lesson(self, controller):
        first = ask(controller, "first")
        first.lesson_ready.emit(OCR, STEPS, "answer")
        pump()
        assert state(controller) is TutorState.TEACHING

        # A worker's signals all precede its finished(), so a late duplicate
        # can only be queued ahead of the finished that releases the worker,
        # never emitted from a worker already deleted.
        first.lesson_ready.emit(OCR, STEPS, "late duplicate")
        first.finish()
        assert ask(controller, "second") is None, "queued behind the finishing first"
        pump()
        flush_deferred_deletes()
        second = FakeWorker.created[-1]

        assert sip.isdeleted(first)
        assert second is not first and second.started
        assert controller.lesson_steps == []
        assert state(controller) is TutorState.ANALYZING


# ------------------------------------------------------------ capture


class TestCancelDuringCapture:
    def test_cancel_inside_capture_discards_the_image(self, controller, monkeypatch):
        def capture_then_cancel(region=None):
            cancel(controller)
            return image(640, 480)

        monkeypatch.setattr(controller.capture_engine, "capture", capture_then_cancel)

        assert ask(controller) is None
        assert FakeWorker.created == []
        assert state(controller) is TutorState.IDLE
        assert controller.current_image is None
        assert controller.worker is None

    def test_cancel_while_the_companion_hides_skips_the_grab(self, controller, monkeypatch):
        monkeypatch.setattr(controller_module, "COMPOSITOR_SETTLE_SECONDS", 0)
        controller.companion.show()
        # Delivered by the processEvents() inside _companion_hidden, before
        # the capture engine is asked for anything.
        QTimer.singleShot(0, lambda: cancel(controller))

        assert ask(controller) is None

        assert controller.captures == []
        assert state(controller) is TutorState.IDLE
        assert controller.companion.isVisible(), "the companion comes back after a cancel"

    def test_capture_error_dialog_cannot_reset_a_newer_request(self, controller, monkeypatch):
        calls = {"n": 0}

        def failing_then_working(region=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("no screen")
            return image()

        monkeypatch.setattr(controller.capture_engine, "capture", failing_then_working)
        # The dialog's nested loop is where the learner asks again.
        monkeypatch.setattr(controller, "_show_error", lambda msg: controller.ask("again"))

        controller.ask("first")

        assert len(FakeWorker.created) == 1
        assert FakeWorker.created[0].question == "again"
        assert state(controller) is TutorState.ANALYZING

    def test_capture_error_without_interference_returns_to_idle(self, controller, monkeypatch):
        monkeypatch.setattr(
            controller.capture_engine, "capture", Mock(side_effect=OSError("no screen"))
        )
        shown = Mock()
        monkeypatch.setattr(controller, "_show_error", shown)

        controller.ask("q")

        shown.assert_called_once()
        assert state(controller) is TutorState.IDLE
        assert FakeWorker.created == []


# ------------------------------------------------------------ dialogs and demos


class TestNestedLoops:
    def test_error_dialog_recheck_blocks_a_fallback_for_a_retired_request(
        self, controller, monkeypatch
    ):
        started = Mock()
        monkeypatch.setattr(controller.demo_manager, "start_demo", started)
        monkeypatch.setattr(controller.demo_manager, "get_available_demos", lambda: {"d": {}})

        def ask_again_then_accept(*args, **kwargs):
            controller.ask("again")
            return QMessageBox.StandardButton.Yes

        monkeypatch.setattr(QMessageBox, "question", ask_again_then_accept)

        first = ask(controller, "first")
        first.error.emit("boom")
        pump()

        started.assert_not_called()
        assert state(controller) is TutorState.ANALYZING
        assert controller._pending_lesson is not None
        assert controller._pending_lesson.question == "again"

    @pytest.mark.parametrize(
        "reply", [QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No], ids=["yes", "no"]
    )
    def test_escape_over_the_error_dialog_cancels_its_request(self, controller, monkeypatch, reply):
        started = Mock()
        monkeypatch.setattr(controller.demo_manager, "start_demo", started)
        monkeypatch.setattr(controller.demo_manager, "get_available_demos", lambda: {"d": {}})
        # A stale draft under the dialog must not turn the Escape into a
        # draft-abandon; the dialog is what the learner is looking at.
        controller.companion.question_input.setText("stale draft")

        def escape_then_reply(*args, **kwargs):
            assert state(controller) is TutorState.IDLE, "the dialog opens over IDLE"
            controller.input_manager.handle_action(InputAction.ESCAPE)
            return reply

        monkeypatch.setattr(QMessageBox, "question", escape_then_reply)

        worker = ask(controller, "first")
        worker.error.emit("boom")
        pump()  # Yes must not start the demo; No must not show a second error

        started.assert_not_called()
        assert controller._active_request is None
        assert controller._modal_request is None
        assert state(controller) is TutorState.IDLE
        assert controller.ui.lbl_status.text() == "Lesson cancelled. Ready."

    def test_nested_error_dialog_does_not_resurrect_the_outer_owner(self, controller, monkeypatch):
        started = Mock()
        monkeypatch.setattr(controller.demo_manager, "start_demo", started)
        monkeypatch.setattr(controller.demo_manager, "get_available_demos", lambda: {"d": {}})
        owners: list[int | None] = []

        def outer_dialog(*args, **kwargs):
            # Inside the first dialog the learner asks again and that request
            # fails too, opening a second dialog that closes before this one.
            monkeypatch.setattr(QMessageBox, "question", inner_dialog)
            controller.ask("again")  # queued behind the still-finishing first
            first.finish()
            pump()  # releases first and starts the replacement
            FakeWorker.created[-1].error.emit("boom again")
            pump()
            owners.append(controller._modal_request)
            return QMessageBox.StandardButton.Yes

        def inner_dialog(*args, **kwargs):
            owners.append(controller._modal_request)
            controller.input_manager.handle_action(InputAction.ESCAPE)
            return QMessageBox.StandardButton.Yes

        monkeypatch.setattr(QMessageBox, "question", outer_dialog)

        first = ask(controller, "first")
        first.error.emit("boom")  # stale by the time its dialog closes
        pump()
        second = FakeWorker.created[-1]

        assert second is not first
        assert owners == [second.request.serial, None], "inner owned, then nobody"
        started.assert_not_called()
        assert controller._modal_request is None
        assert state(controller) is TutorState.IDLE

    def test_finished_during_a_new_capture_leaves_the_new_request_in_charge(
        self, controller, monkeypatch
    ):
        first = ask(controller, "first")
        cancel(controller)

        def finish_first_then_capture(region=None):
            # The companion hide pumps events, which is where a cancelled
            # worker's finished() can land in the middle of a newer capture.
            first.finish()
            pump()
            # Checked here, mid-capture: once the new worker starts it disables
            # the button again, so the final assertion alone would miss this.
            assert not controller.ui.btn_capture.isEnabled()
            return image()

        monkeypatch.setattr(controller.capture_engine, "capture", finish_first_then_capture)

        second = ask(controller, "second")

        assert second is not None and second.started, "started directly, not queued"
        assert controller.worker is second
        assert controller._pending_lesson is None
        assert state(controller) is TutorState.ANALYZING
        assert not controller.ui.btn_capture.isEnabled()

    def test_starting_a_demo_retires_the_running_request(self, controller, monkeypatch):
        def fake_demo(demo_id):
            controller.demo_manager.is_running = True
            controller.demo_manager.demo_started.emit("demo/x/screenshot.png")

        monkeypatch.setattr(controller.demo_manager, "start_demo", fake_demo)

        worker = ask(controller)
        controller.start_demo("x")
        assert worker.interrupted

        worker.lesson_ready.emit(OCR, STEPS, "answer")
        pump()

        assert controller.lesson_steps == []
        assert state(controller) is TutorState.TEACHING, "the demo, not the lesson, is on screen"
        assert controller.current_image is None


# ------------------------------------------------------------ threads


class TestThreadDelivery:
    def _run_until(self, condition, timeout_ms=5000):
        loop = QEventLoop()
        poll = QTimer()
        poll.setInterval(5)
        poll.timeout.connect(lambda: condition() and loop.quit())
        guard = QTimer()
        guard.setSingleShot(True)
        guard.timeout.connect(loop.quit)
        poll.start()
        guard.start(timeout_ms)
        loop.exec()
        poll.stop()
        assert condition(), "timed out waiting for the worker"

    def test_results_from_a_real_thread_are_handled_on_the_gui_thread(
        self, controller, monkeypatch
    ):
        monkeypatch.setattr(controller_module, "LessonWorker", ThreadedFakeWorker)
        seen: list[QThread] = []
        present = controller._on_lesson_finished

        def record_thread(ocr_data, steps, answer):
            seen.append(QThread.currentThread())
            present(ocr_data, steps, answer)

        monkeypatch.setattr(controller, "_on_lesson_finished", record_thread)

        controller.ask("q")
        self._run_until(lambda: controller.worker is None)

        assert seen == [QApplication.instance().thread()]
        assert state(controller) is TutorState.TEACHING

    def test_worker_cancelled_during_ocr_does_not_ask_the_model(self, qt_app, monkeypatch):
        worker = LessonWorker(LessonRequest(1, "q", image()))

        def ocr_then_cancel(_image):
            worker.requestInterruption()
            return OCR

        monkeypatch.setattr(controller_module, "extract_ocr_data", ocr_then_cancel)
        engine = Mock(side_effect=AssertionError("model stage started after cancel"))
        monkeypatch.setattr(engine_module, "LessonEngine", engine)
        results: list[object] = []
        worker.lesson_ready.connect(lambda *a: results.append(a))
        worker.error.connect(lambda m: results.append(m))

        worker.start()
        assert worker.wait(5000)
        pump()

        engine.assert_not_called()
        assert results == []
