"""Selecting an area only prepares the next capture; Ask is the explicit action.

Everything runs through a real DesktopController and a real RegionSelector on
the offscreen platform. Capture is a fake that records the region it was
asked for, workers are fakes the test drives, and the keyboard backend is a
fake so no global hook is installed. Native focus and drag behaviour on
Windows is a hand check, not something these tests can establish.
"""

from __future__ import annotations

import dataclasses
from unittest.mock import Mock

import pytest
from PIL import Image
from PyQt6 import sip
from PyQt6.QtCore import QEvent, QPoint, Qt, QTimer
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMessageBox

import src.desktop.controller as controller_module
import src.input.hotkeys as hotkeys_module
from src.attention.coordinates import CaptureGeometry
from src.desktop.controller import CROPPED_CAPTURE_NOTE, DesktopController, LessonRequest
from src.desktop.selection import SelectedRegion
from src.input.events import InputAction
from src.input.state_machine import TutorState
from src.locator import STEP_LOCATION_KEY
from tests.unit.test_escape import KEY_DOWN, KEY_UP, FakeKeyboard
from tests.unit.test_lesson_cancellation import OCR, FakeWorker, flush_deferred_deletes, pump

BOX = {"left": 10, "top": 10, "width": 20, "height": 5}
STEPS = [
    {
        "step": 1,
        "title": "t",
        "anchor": "x",
        "attention": "rectangle",
        "explanation": "e",
        STEP_LOCATION_KEY: {"box": BOX, "source": "ocr", "confidence": 1.0},
    }
]


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def backend(monkeypatch):
    fake = FakeKeyboard()
    monkeypatch.setattr(hotkeys_module, "keyboard", fake)
    return fake


@pytest.fixture
def controller(qt_app, backend, monkeypatch):
    FakeWorker.created = []
    monkeypatch.setattr(controller_module, "LessonWorker", FakeWorker)
    monkeypatch.setattr(controller_module, "COMPOSITOR_SETTLE_SECONDS", 0)
    c = DesktopController()
    c.captures = []

    def fake_capture(region=None):
        c.captures.append(region)
        if region is None:
            return Image.new("RGB", (800, 600))
        return Image.new("RGB", (region["width"], region["height"]))

    monkeypatch.setattr(c.capture_engine, "capture", fake_capture)
    # The suite runs under WSL, where the real engine refuses regions.
    monkeypatch.setattr(c.capture_engine, "supports_regions", lambda: True)
    monkeypatch.setattr(
        c, "_show_error", Mock(side_effect=AssertionError("unexpected error dialog"))
    )
    monkeypatch.setattr(
        QMessageBox, "question", Mock(side_effect=AssertionError("unexpected question dialog"))
    )
    c.companion.show()
    yield c
    c.hotkeys.stop()
    if c._selection is not None:
        c._cancel_selection()
    c.overlay.animation_engine.stop()
    c.companion.close()
    c.ui.close()
    c.overlay.close()


def state(c) -> TutorState:
    return c.input_manager.current_state


def selector(c):
    assert c._selection is not None, "no selection in progress"
    return c._selection.selector


def drag(widget, start: tuple[int, int], end: tuple[int, int]) -> None:
    QTest.mousePress(widget, Qt.MouseButton.LeftButton, pos=QPoint(*start))
    QTest.mouseMove(widget, QPoint(*end))
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=QPoint(*end))


def select(c, start=(100, 50), end=(300, 250)):
    c.input_manager.handle_action(InputAction.SELECT_REGION)
    drag(selector(c), start, end)


def ask(c, question="q") -> FakeWorker | None:
    before = len(FakeWorker.created)
    c.ask(question)
    return FakeWorker.created[before] if len(FakeWorker.created) > before else None


def drawn(c):
    return [(s.x, s.y, s.width, s.height) for s in c.overlay.animation_engine.shapes]


def escape_event(auto_repeat=False) -> QKeyEvent:
    return QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier, "", auto_repeat
    )


def screen_region(c):
    return controller_module.physical_region(c.overlay.screen_target)


def cancel(c):
    c.input_manager.handle_action(InputAction.CANCEL_LESSON)


def panels_visible(c) -> dict[str, bool]:
    return {
        "companion": c.companion.isVisible(),
        "dev_panel": c.ui.isVisible(),
        "overlay": c.overlay.isVisible(),
    }


class DeadScreen:
    """A QScreen whose C++ object is gone, as after unplugging that monitor.

    Every accessor raises the RuntimeError sip raises for a deleted wrapper.
    """

    def _gone(self):
        raise RuntimeError("wrapped C/C++ object of type QScreen has been deleted")

    def geometry(self):
        self._gone()

    def name(self):
        self._gone()

    def devicePixelRatio(self):
        self._gone()


# ------------------------------------------------------------ starting


class TestStartingSelection:
    def test_button_opens_the_selector_and_hides_the_panels(self, controller):
        controller.companion.btn_select_area.click()

        assert state(controller) is TutorState.SELECTING
        assert selector(controller).isVisible()
        assert not controller.companion.isVisible()
        assert not controller.overlay.isVisible()

    def test_shortcut_opens_the_selector(self, controller, backend, qt_app):
        controller.hotkeys.start()

        backend.press_chord("ctrl+shift+s")
        qt_app.processEvents()

        assert state(controller) is TutorState.SELECTING

    @pytest.mark.parametrize("busy", [TutorState.CAPTURING, TutorState.ANALYZING])
    def test_selection_is_refused_while_busy(self, controller, busy):
        controller.input_manager.set_state(busy)

        controller.input_manager.handle_action(InputAction.SELECT_REGION)

        assert controller._selection is None
        assert state(controller) is busy

    def test_repeated_selection_is_refused_while_selecting(self, controller):
        controller.input_manager.handle_action(InputAction.SELECT_REGION)
        first = selector(controller)

        controller.input_manager.handle_action(InputAction.SELECT_REGION)

        assert selector(controller) is first

    def test_unsupported_backend_reports_and_captures_nothing(self, controller, monkeypatch):
        monkeypatch.setattr(controller.capture_engine, "supports_regions", lambda: False)

        controller.input_manager.handle_action(InputAction.SELECT_REGION)

        assert controller._selection is None
        assert state(controller) is TutorState.IDLE
        assert controller.captures == []
        assert "unavailable" in controller.companion.lbl_title.text().lower()

    def test_active_demo_is_stopped_before_selecting(self, controller):
        controller.demo_manager.is_running = True
        controller.input_manager.set_state(TutorState.TEACHING)

        controller.input_manager.handle_action(InputAction.SELECT_REGION)

        assert not controller.demo_manager.is_running
        assert state(controller) is TutorState.SELECTING

    def test_in_flight_worker_is_retired_but_the_shown_lesson_stays(self, controller):
        worker = ask(controller, "first")
        # A lesson is on screen while this worker is still running.
        controller.lesson_steps = STEPS
        controller.input_manager.set_state(TutorState.TEACHING)

        controller.input_manager.handle_action(InputAction.SELECT_REGION)
        worker.lesson_ready.emit(OCR, [{"step": 9, "title": "late", "anchor": "x"}], "late")
        pump()

        assert worker.interrupted
        assert controller.lesson_steps == STEPS, "late worker could not take over"
        assert state(controller) is TutorState.SELECTING


# ------------------------------------------------------------ finishing


class TestConfirmingSelection:
    def test_valid_drag_prepares_the_area_without_capturing(self, controller):
        controller.companion.question_input.setText("half a quest")

        select(controller)

        assert state(controller) is TutorState.IDLE
        assert controller._selection is None
        assert controller.captures == []
        assert FakeWorker.created == []
        assert controller.companion.question_input.text() == "half a quest"
        assert controller.companion.isVisible()
        assert controller.companion.area_widget.isVisibleTo(controller.companion)
        assert "200 × 200" in controller.companion.lbl_area.text()

    def test_reversed_drag_is_the_same_rectangle(self, controller):
        select(controller, start=(300, 250), end=(100, 50))

        assert controller._selected_region.geometry.region() == {
            "left": 100,
            "top": 50,
            "width": 200,
            "height": 200,
        }

    def test_zero_area_drag_cancels_and_keeps_the_previous_area(self, controller):
        select(controller)
        kept = controller._selected_region
        controller.companion.question_input.setText("draft")

        select(controller, start=(40, 40), end=(40, 90))

        assert controller._selected_region is kept
        assert state(controller) is TutorState.IDLE
        assert controller.companion.question_input.text() == "draft"
        assert controller.captures == []

    def test_selector_is_disposed_after_use(self, controller):
        controller.input_manager.handle_action(InputAction.SELECT_REGION)
        widget = selector(controller)

        drag(widget, (0, 0), (50, 50))
        pump()
        flush_deferred_deletes()

        assert sip.isdeleted(widget)

    def test_stale_selector_callback_has_no_effect(self, controller):
        controller.input_manager.handle_action(InputAction.SELECT_REGION)
        old = selector(controller)
        controller._cancel_selection()
        assert state(controller) is TutorState.IDLE

        controller._on_area_selected(old, 0, 0, 50, 50)
        controller._on_selection_cancelled(old)

        assert controller._selected_region is None
        assert state(controller) is TutorState.IDLE

    def test_changed_screen_at_release_rejects_the_area(self, controller, monkeypatch):
        controller.input_manager.handle_action(InputAction.SELECT_REGION)
        original = controller._current_screen_signature()
        monkeypatch.setattr(
            controller,
            "_current_screen_signature",
            lambda: dataclasses.replace(original, device_pixel_ratio=1.5),
        )

        drag(selector(controller), (100, 50), (300, 250))

        assert controller._selected_region is None
        assert state(controller) is TutorState.IDLE
        assert controller.captures == []

    def test_screen_removed_at_release_rejects_the_area_without_raising(self, controller):
        controller.input_manager.handle_action(InputAction.SELECT_REGION)
        controller.overlay.screen_target = DeadScreen()

        # A RuntimeError escaping here would abort the Qt slot and leave the
        # state machine stuck in SELECTING.
        drag(selector(controller), (100, 50), (300, 250))

        assert controller._selected_region is None
        assert controller._selection is None
        assert state(controller) is TutorState.IDLE
        assert controller.captures == []
        assert FakeWorker.created == []
        assert "again" in controller.companion.lbl_title.text().lower()

    def test_selection_is_refused_on_a_removed_screen(self, controller):
        controller.overlay.screen_target = DeadScreen()

        controller.input_manager.handle_action(InputAction.SELECT_REGION)

        assert controller._selection is None
        assert state(controller) is TutorState.IDLE
        assert controller.companion.isVisible()


class TestPanelsDuringSelection:
    def test_developer_panel_is_hidden_while_selecting_and_restored(self, controller):
        controller.ui.show()
        controller.overlay.show()

        controller.input_manager.handle_action(InputAction.SELECT_REGION)
        assert panels_visible(controller) == {
            "companion": False,
            "dev_panel": False,
            "overlay": False,
        }

        controller._cancel_selection()

        assert panels_visible(controller) == {
            "companion": True,
            "dev_panel": True,
            "overlay": True,
        }

    def test_hidden_developer_panel_stays_hidden_after_selecting(self, controller):
        assert not controller.ui.isVisible()

        select(controller)

        assert not controller.ui.isVisible()
        assert controller.companion.isVisible()


class TestCancellingSelection:
    def test_global_escape_cancels_only_the_selection(self, controller, backend, qt_app):
        controller.hotkeys.start()
        controller.lesson_steps = STEPS
        controller.input_manager.set_state(TutorState.TEACHING)
        controller.companion.question_input.setText("half a quest")
        controller.input_manager.handle_action(InputAction.SELECT_REGION)
        widget = selector(controller)

        # Local Qt Escape first, held repeats, then the one release the hook sees.
        qt_app.sendEvent(widget, escape_event())
        for _ in range(3):
            qt_app.sendEvent(widget, escape_event(auto_repeat=True))
            backend.deliver("esc", KEY_DOWN)
        assert state(controller) is TutorState.SELECTING, "local input defers to the hook"
        backend.deliver("esc", KEY_UP)
        qt_app.processEvents()

        assert state(controller) is TutorState.TEACHING
        assert controller.lesson_steps == STEPS
        assert controller.companion.question_input.text() == "half a quest"
        assert controller.captures == []

    def test_local_escape_cancels_when_no_hook_and_ignores_repeats(self, controller, qt_app):
        assert not controller.hotkeys.escape_hooked
        controller.input_manager.handle_action(InputAction.SELECT_REGION)
        widget = selector(controller)

        qt_app.sendEvent(widget, escape_event(auto_repeat=True))
        assert state(controller) is TutorState.SELECTING
        qt_app.sendEvent(widget, escape_event())

        assert state(controller) is TutorState.IDLE
        assert controller.companion.isVisible()

    def test_closing_the_selector_returns_to_question_entry(self, controller):
        controller.input_manager.handle_action(InputAction.SELECT_REGION)

        selector(controller).close()

        assert state(controller) is TutorState.IDLE
        assert controller._selection is None

    def test_ask_and_navigation_are_blocked_while_selecting(self, controller):
        controller.input_manager.handle_action(InputAction.SELECT_REGION)

        controller.ask("q")
        controller.input_manager.handle_action(InputAction.CAPTURE_SCREEN)
        controller.input_manager.handle_action(InputAction.TOGGLE_DEBUG)
        controller.input_manager.handle_action(InputAction.NEXT_STEP)

        assert controller.captures == []
        assert FakeWorker.created == []
        assert state(controller) is TutorState.SELECTING

    def test_finished_worker_does_not_enable_capture_mid_selection(self, controller):
        worker = ask(controller)
        controller.input_manager.handle_action(InputAction.CANCEL_LESSON)
        controller.input_manager.handle_action(InputAction.SELECT_REGION)

        worker.finish()
        pump()

        assert not controller.ui.btn_capture.isEnabled()
        assert state(controller) is TutorState.SELECTING


# ------------------------------------------------------------ asking


class TestAskingWithASelection:
    def test_ask_sends_the_exact_region_question_and_geometry(self, controller):
        select(controller)
        expected = CaptureGeometry.from_logical_rect(
            100, 50, 200, 200, screen_region(controller), 1.0
        )

        worker = ask(controller, "what is this")

        assert controller.captures == [expected.region()]
        assert worker is not None
        assert worker.request.question == "what is this"
        assert worker.request.geometry == expected
        assert worker.image.size == (200, 200)

    def test_the_area_is_kept_for_later_asks(self, controller):
        select(controller)
        first = ask(controller, "one")
        # The first lesson is delivered and shown; only then can a second be asked.
        first.lesson_ready.emit(OCR, STEPS, "answer")
        first.finish()
        pump()
        assert state(controller) is TutorState.TEACHING

        ask(controller, "two")

        assert len(controller.captures) == 2
        assert controller.captures[0] == controller.captures[1]

    def test_full_screen_ask_without_selection_is_unchanged(self, controller):
        worker = ask(controller)

        assert controller.captures == [screen_region(controller)]
        assert worker.request.geometry is None

    def test_wsl_full_screen_capture_passes_no_region(self, controller, monkeypatch):
        monkeypatch.setattr(controller.capture_engine, "supports_regions", lambda: False)

        ask(controller)

        assert controller.captures == [None]

    def test_changed_dpr_rejects_the_ask_before_any_capture(self, controller, monkeypatch):
        select(controller)
        controller.companion.question_input.setText("draft")
        original = controller._current_screen_signature()
        monkeypatch.setattr(
            controller,
            "_current_screen_signature",
            lambda: dataclasses.replace(original, device_pixel_ratio=1.25),
        )

        assert ask(controller, "q") is None

        assert controller.captures == []
        assert state(controller) is TutorState.IDLE
        assert controller._selected_region is not None, "the learner decides: reselect or clear"
        assert "again" in controller.companion.lbl_title.text().lower()

    def test_screen_change_while_the_companion_hides_rejects_the_crop(
        self, controller, monkeypatch
    ):
        select(controller)
        original = controller._current_screen_signature()
        signatures = iter([original, dataclasses.replace(original, width=1024)])
        # Valid at the pre-flight check, changed by the time the grab would run.
        monkeypatch.setattr(controller, "_current_screen_signature", lambda: next(signatures))

        assert ask(controller) is None

        assert controller.captures == []
        assert state(controller) is TutorState.IDLE

    def test_screen_removed_before_ask_rejects_without_capture_or_raising(self, controller):
        select(controller)
        controller.overlay.screen_target = DeadScreen()

        assert ask(controller, "q") is None

        assert controller.captures == []
        assert FakeWorker.created == []
        assert state(controller) is TutorState.IDLE
        assert "again" in controller.companion.lbl_title.text().lower()

    def test_every_visible_panel_is_hidden_for_the_grab_and_restored(self, controller, monkeypatch):
        controller.ui.show()
        controller.overlay.show()
        seen = []

        def capture(region=None):
            seen.append(panels_visible(controller))
            return Image.new("RGB", (region["width"], region["height"]))

        monkeypatch.setattr(controller.capture_engine, "capture", capture)
        select(controller)

        worker = ask(controller)

        assert worker is not None
        assert seen == [{"companion": False, "dev_panel": False, "overlay": False}]
        assert panels_visible(controller) == {
            "companion": True,
            "dev_panel": True,
            "overlay": True,
        }

    def test_panels_hidden_before_the_grab_stay_hidden_after_it(self, controller, monkeypatch):
        controller.ui.hide()
        controller.overlay.hide()
        seen = []

        def capture(region=None):
            seen.append(panels_visible(controller))
            return Image.new("RGB", (800, 600))

        monkeypatch.setattr(controller.capture_engine, "capture", capture)

        ask(controller)

        assert seen == [{"companion": False, "dev_panel": False, "overlay": False}]
        assert panels_visible(controller) == {
            "companion": True,
            "dev_panel": False,
            "overlay": False,
        }

    def test_cancel_while_the_panels_hide_restores_all_of_them(self, controller):
        controller.ui.show()
        controller.overlay.show()
        select(controller)
        # Delivered by the processEvents() inside the hide, before the grab.
        QTimer.singleShot(0, lambda: cancel(controller))

        assert ask(controller) is None

        assert controller.captures == []
        assert state(controller) is TutorState.IDLE
        assert panels_visible(controller) == {
            "companion": True,
            "dev_panel": True,
            "overlay": True,
        }

    def test_clear_restores_full_screen_for_the_next_ask(self, controller):
        select(controller)

        controller.companion.btn_clear_area.click()
        ask(controller)

        assert controller._selected_region is None
        assert not controller.companion.area_widget.isVisibleTo(controller.companion)
        assert controller.captures == [screen_region(controller)]

    def test_worker_is_told_the_image_is_a_crop(self, qt_app, monkeypatch):
        geometry = CaptureGeometry.from_logical_rect(
            0, 0, 10, 10, {"left": 0, "top": 0, "width": 100, "height": 100}, 1.0
        )
        seen = {}

        class Engine:
            def __init__(self, *a, **k):
                pass

            def generate_lesson(self, question, history, explanation):
                seen["args"] = (question, explanation)
                return "a", None, []

        import src.lesson_engine as engine_module

        monkeypatch.setattr(engine_module, "LessonEngine", Engine)
        monkeypatch.setattr(controller_module, "extract_ocr_data", lambda img: OCR)

        controller_module.LessonWorker(
            LessonRequest(1, "q", Image.new("RGB", (10, 10)), geometry)
        ).run()
        assert seen["args"] == ("q", CROPPED_CAPTURE_NOTE)
        controller_module.LessonWorker(LessonRequest(2, "q", Image.new("RGB", (10, 10)))).run()
        assert seen["args"] == ("q", "")


# ------------------------------------------------------------ rendering


def crop_request(serial, geometry):
    return LessonRequest(serial, "q", Image.new("RGB", (geometry.width, geometry.height)), geometry)


class TestCropRendering:
    @pytest.mark.parametrize(
        "ratio, expected",
        [(1.0, (110, 60, 20, 5)), (1.5, (107, 57, 13, 3))],
        ids=["dpr1", "dpr1.5"],
    )
    def test_crop_boxes_land_at_their_physical_origin(self, controller, ratio, expected):
        screen = {"left": 0, "top": 0, "width": 3000, "height": 2000}
        geometry = CaptureGeometry.from_logical_rect(100, 50, 200, 200, screen, ratio)
        controller._selected_region = SelectedRegion(
            geometry, controller._current_screen_signature()
        )
        controller.capture_engine.capture = lambda region=None: Image.new(
            "RGB", (region["width"], region["height"])
        )
        controller._selection_still_valid = lambda selected: True

        worker = ask(controller)
        worker.lesson_ready.emit(OCR, STEPS, "answer")
        pump()

        assert controller.overlay.capture_geometry == geometry
        assert drawn(controller) == [expected]

    def test_clearing_the_area_does_not_move_the_shown_crop(self, controller):
        select(controller)
        worker = ask(controller)
        worker.lesson_ready.emit(OCR, STEPS, "answer")
        pump()
        before = drawn(controller)

        controller.input_manager.handle_action(InputAction.CLEAR_REGION)
        controller.show_current_step()

        assert drawn(controller) == before == [(110, 60, 20, 5)]
        assert controller.overlay.capture_geometry is not None

    def test_debug_toggle_keeps_the_crop_mapping_both_ways(self, controller):
        select(controller)
        worker = ask(controller)
        worker.lesson_ready.emit(OCR, STEPS, "answer")
        pump()
        geometry = controller.overlay.capture_geometry

        controller.toggle_debug()
        assert controller.is_debug_mode
        assert controller.overlay.capture_geometry == geometry
        assert controller.overlay.show_bg

        controller.toggle_debug()
        assert controller.overlay.capture_geometry == geometry
        assert drawn(controller) == [(110, 60, 20, 5)]

    def test_full_screen_lesson_after_a_crop_restores_fit_mapping(self, controller):
        select(controller)
        worker = ask(controller)
        worker.lesson_ready.emit(OCR, STEPS, "answer")
        pump()

        worker.finish()
        pump()
        controller.input_manager.handle_action(InputAction.CLEAR_REGION)
        second = ask(controller)
        second.lesson_ready.emit(OCR, STEPS, "answer")
        pump()

        assert controller.overlay.capture_geometry is None
        assert second.request.geometry is None
        assert drawn(controller) == [(10, 10, 20, 5)]

    def test_late_result_cannot_change_the_current_mapping(self, controller):
        select(controller)
        first = ask(controller)
        # Cancelled but not yet finished: the next ask queues behind it.
        controller.input_manager.handle_action(InputAction.CANCEL_LESSON)
        controller.input_manager.handle_action(InputAction.CLEAR_REGION)
        second = ask(controller)  # queued behind first; full screen
        assert second is None
        assert state(controller) is TutorState.ANALYZING
        first.lesson_ready.emit(OCR, STEPS, "stale crop")
        first.finish()
        pump()
        second = FakeWorker.created[-1]
        assert second is not first
        second.lesson_ready.emit(OCR, STEPS, "answer")
        pump()

        assert controller.overlay.capture_geometry is None
        assert drawn(controller) == [(10, 10, 20, 5)]


class TestScreenChangeDuringAnalysis:
    def test_request_carries_the_capture_time_signature(self, controller):
        select(controller)

        worker = ask(controller)

        assert worker.request.signature == controller._selected_region.signature
        assert worker.request.signature == controller._current_screen_signature()

    def test_changed_screen_after_capture_drops_the_result_and_asks_to_reselect(
        self, controller, monkeypatch
    ):
        select(controller)
        worker = ask(controller)
        original = controller._current_screen_signature()
        monkeypatch.setattr(
            controller,
            "_current_screen_signature",
            lambda: dataclasses.replace(original, device_pixel_ratio=1.5),
        )

        worker.lesson_ready.emit(OCR, STEPS, "answer")
        pump()

        assert controller.lesson_steps == []
        assert drawn(controller) == []
        assert controller.overlay.capture_geometry is None
        assert state(controller) is TutorState.IDLE
        assert controller.ui.btn_capture.isEnabled()
        assert controller._selected_region is not None, "the learner decides: reselect or clear"
        assert "again" in controller.companion.lbl_title.text().lower()

    def test_result_is_judged_against_its_own_snapshot_not_the_current_selection(self, controller):
        select(controller)
        worker = ask(controller)
        # Cleared while the worker runs: the request's own snapshot still matches.
        controller.input_manager.handle_action(InputAction.CLEAR_REGION)

        worker.lesson_ready.emit(OCR, STEPS, "answer")
        pump()

        assert state(controller) is TutorState.TEACHING
        assert drawn(controller) == [(110, 60, 20, 5)]

    def test_superseded_stale_result_does_not_disturb_the_newer_request(
        self, controller, monkeypatch
    ):
        select(controller)
        first = ask(controller)
        cancel(controller)
        controller.input_manager.handle_action(InputAction.CLEAR_REGION)
        assert ask(controller, "newer") is None, "queued behind the stopping worker"
        pending = controller._pending_lesson
        title_before = controller.companion.lbl_title.text()
        original = controller._current_screen_signature()
        monkeypatch.setattr(
            controller,
            "_current_screen_signature",
            lambda: dataclasses.replace(original, width=1024),
        )

        first.lesson_ready.emit(OCR, STEPS, "stale crop")
        pump()

        assert state(controller) is TutorState.ANALYZING
        assert controller._pending_lesson is pending
        assert not controller.ui.btn_capture.isEnabled()
        assert controller.companion.lbl_title.text() == title_before

        first.finish()
        pump()
        second = FakeWorker.created[-1]
        assert second is not first and second.started
        assert second.request.question == "newer"
