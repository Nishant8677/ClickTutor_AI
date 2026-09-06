"""One physical Escape acts once, whichever route delivers it.

Escape reaches the controller two ways: the global keyboard hook, delivered
on the listener thread and queued to the GUI thread as an ESCAPE action, and
Qt key events on whichever ClickTutor window has focus. The global hook is
the authority whenever it is installed; the Qt events stand in only when it
is not. These tests drive the real controller under the offscreen platform
plugin with a fake keyboard backend, so no global hook is installed and no
screen is captured.

Focus is modelled explicitly by patching question_has_focus. Offscreen Qt
focus says nothing about what Windows does with a focus request from a global
hook, and that is the case the arming window exists for.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication

import src.input.hotkeys as hotkeys_module
from src.desktop.controller import DesktopController
from src.input.events import InputAction
from src.input.hotkeys import HotkeyManager
from src.input.state_machine import TutorState

KEY_DOWN = "down"
KEY_UP = "up"


class _Event:
    def __init__(self, event_type: str, scan_code: int = 1) -> None:
        self.event_type = event_type
        self.scan_code = scan_code


class FakeKeyboard:
    """Records registrations the way the keyboard module would, without hooking.

    `fail_on` names a registration (a chord or key) whose registration raises,
    to model a partial startup failure. `fail_remove` names one whose removal
    raises and leaves the callback installed, as a failed unhook would.
    """

    KEY_DOWN = KEY_DOWN
    KEY_UP = KEY_UP

    def __init__(self, fail_on: str | None = None, fail_remove: str | None = None) -> None:
        self.fail_on = fail_on
        self.fail_remove = fail_remove
        self.hotkeys: dict[str, object] = {}
        self.key_hooks: dict[str, object] = {}
        self.removed: list[str] = []

    def add_hotkey(self, chord, callback):
        if chord == self.fail_on:
            raise OSError(f"cannot hook {chord}")
        self.hotkeys[chord] = callback
        return ("hotkey", chord)

    def hook_key(self, key, callback):
        if key == self.fail_on:
            raise OSError(f"cannot hook {key}")
        self.key_hooks[key] = callback
        return ("key", key)

    def remove_hotkey(self, handle):
        _, chord = handle
        if chord == self.fail_remove:
            raise OSError(f"cannot remove {chord}")
        del self.hotkeys[chord]
        self.removed.append(chord)

    def unhook(self, handle):
        _, key = handle
        if key == self.fail_remove:
            raise OSError(f"cannot unhook {key}")
        del self.key_hooks[key]
        self.removed.append(key)

    @property
    def registered(self) -> set[str]:
        return set(self.hotkeys) | set(self.key_hooks)

    # -- driving the fake from a test ------------------------------------

    def deliver(self, key: str, event_type: str) -> None:
        """Invokes the per-key hook as keyboard would, on the caller's thread."""
        self.key_hooks[key](_Event(event_type))

    def press_chord(self, chord: str) -> None:
        self.hotkeys[chord]()


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def _escape_event(auto_repeat: bool = False) -> QKeyEvent:
    return QKeyEvent(
        QEvent.Type.KeyPress,
        Qt.Key.Key_Escape,
        Qt.KeyboardModifier.NoModifier,
        "",
        auto_repeat,
    )


# ------------------------------------------------------------------ hooks


class TestHotkeyRegistration:
    def test_escape_is_hooked_per_key_not_as_a_hotkey(self):
        # add_hotkey(trigger_on_release=True) loses the release in the
        # installed keyboard source; a per-key hook dispatches on scan code.
        backend = FakeKeyboard()
        manager = HotkeyManager(backend=backend)

        assert manager.start() is True
        assert "esc" in backend.key_hooks
        assert "esc" not in backend.hotkeys
        assert manager.escape_hooked

    def test_release_fires_escape_once_and_held_repeats_do_not(self):
        backend = FakeKeyboard()
        manager = HotkeyManager(backend=backend)
        manager.start()
        seen = []
        manager.action_triggered.connect(seen.append)

        # A held key repeats KEY_DOWN and releases once.
        for _ in range(4):
            backend.deliver("esc", KEY_DOWN)
        backend.deliver("esc", KEY_UP)

        assert seen == [InputAction.ESCAPE]

    def test_chords_still_fire_their_actions(self):
        backend = FakeKeyboard()
        manager = HotkeyManager(backend=backend)
        manager.start()
        seen = []
        manager.action_triggered.connect(seen.append)

        backend.press_chord("ctrl+shift+a")
        backend.press_chord("ctrl+shift+s")
        backend.press_chord("ctrl+shift+d")

        assert seen == [
            InputAction.CAPTURE_SCREEN,
            InputAction.SELECT_REGION,
            InputAction.TOGGLE_DEBUG,
        ]

    def test_partial_failure_rolls_back_and_reports_escape_unhooked(self):
        backend = FakeKeyboard(fail_on="esc")
        manager = HotkeyManager(backend=backend)

        assert manager.start() is False
        assert backend.registered == set()
        assert sorted(backend.removed) == ["ctrl+shift+a", "ctrl+shift+d", "ctrl+shift+s"]
        assert not manager.escape_hooked
        assert not manager.is_running

    def test_failure_on_the_first_registration_leaves_nothing_behind(self):
        backend = FakeKeyboard(fail_on="ctrl+shift+a")
        manager = HotkeyManager(backend=backend)

        assert manager.start() is False
        assert backend.registered == set()
        assert not manager.escape_hooked

    def test_stop_removes_exactly_what_was_registered(self):
        backend = FakeKeyboard()
        manager = HotkeyManager(backend=backend)
        manager.start()

        manager.stop()

        assert backend.registered == set()
        assert not manager.escape_hooked
        assert not manager.is_running

    def test_stop_reports_success_when_every_hook_was_removed(self):
        backend = FakeKeyboard()
        manager = HotkeyManager(backend=backend)
        manager.start()

        assert manager.stop() is True

    def test_stop_without_start_is_a_no_op(self):
        backend = FakeKeyboard()
        HotkeyManager(backend=backend).stop()

        assert backend.removed == []

    def test_escape_callback_surviving_a_failed_unhook_is_inert(self):
        # The OS may keep a hook whose removal raised. Its callback belongs to
        # a retired run: it must not emit after stop, nor after a restart has
        # installed the current hook, which alone may act.
        backend = FakeKeyboard(fail_remove="esc")
        manager = HotkeyManager(backend=backend)
        manager.start()
        old_callback = backend.key_hooks["esc"]
        seen = []
        manager.action_triggered.connect(seen.append)

        assert manager.stop() is False
        assert "esc" in backend.key_hooks
        assert not manager.escape_hooked
        assert not manager.is_running
        old_callback(_Event(KEY_UP))
        assert seen == []

        assert manager.start() is True
        assert manager.escape_hooked
        old_callback(_Event(KEY_UP))
        assert seen == []
        backend.deliver("esc", KEY_UP)
        assert seen == [InputAction.ESCAPE]

    def test_chord_callback_surviving_a_failed_removal_is_inert(self):
        backend = FakeKeyboard(fail_remove="ctrl+shift+a")
        manager = HotkeyManager(backend=backend)
        manager.start()
        old_callback = backend.hotkeys["ctrl+shift+a"]
        seen = []
        manager.action_triggered.connect(seen.append)

        assert manager.stop() is False
        assert "ctrl+shift+a" in backend.hotkeys
        old_callback()
        assert seen == []

        assert manager.start() is True
        old_callback()
        assert seen == []
        backend.press_chord("ctrl+shift+a")
        assert seen == [InputAction.CAPTURE_SCREEN]

    def test_failed_removal_during_rollback_leaves_the_callback_inert(self):
        # A partial start rolls back; a chord whose removal fails then must
        # not fire either, because the run it belongs to never went live.
        backend = FakeKeyboard(fail_on="esc", fail_remove="ctrl+shift+d")
        manager = HotkeyManager(backend=backend)
        seen = []
        manager.action_triggered.connect(seen.append)

        assert manager.start() is False
        assert "ctrl+shift+d" in backend.hotkeys
        backend.press_chord("ctrl+shift+d")

        assert seen == []

    def test_start_twice_does_not_register_twice(self):
        backend = FakeKeyboard()
        manager = HotkeyManager(backend=backend)
        manager.start()
        manager.start()

        assert len(backend.hotkeys) == 3
        assert len(backend.key_hooks) == 1

    def test_defaults_to_the_keyboard_module(self):
        assert HotkeyManager()._backend is hotkeys_module.keyboard


# ------------------------------------------------------------- controller


@pytest.fixture
def backend(monkeypatch):
    fake = FakeKeyboard()
    monkeypatch.setattr(hotkeys_module, "keyboard", fake)
    return fake


@pytest.fixture
def controller(qt_app, backend, monkeypatch):
    """A real DesktopController with every live entry point guarded.

    The hook is *not* started here; tests choose whether the global Escape
    hook is the authority by calling controller.hotkeys.start().
    """
    c = DesktopController()
    c.captures = []
    c.errors = []
    c.asked = []
    c.handled = []

    def no_capture(*a, **k):
        c.captures.append((a, k))
        raise AssertionError("screen capture must not run in unit tests")

    monkeypatch.setattr(c.capture_engine, "capture", no_capture)
    monkeypatch.setattr(c, "generate_lesson", lambda q: c.errors.append(("generate", q)))
    monkeypatch.setattr(c, "_show_error", c.errors.append)
    # question_submitted was bound to the real ask() in __init__; rebind it so
    # a submission is recorded instead of starting a capture.
    c.companion.question_submitted.disconnect()
    c.companion.question_submitted.connect(c.asked.append)
    monkeypatch.setattr(c.companion, "question_has_focus", lambda: c.field_focused)
    c.field_focused = False

    real_handle = c.input_manager.handle_action

    def recording_handle(action):
        c.handled.append(action)
        real_handle(action)

    monkeypatch.setattr(c.input_manager, "handle_action", recording_handle)
    # The hook signal was connected to the original bound method in __init__;
    # reconnect so queued deliveries are recorded too.
    c.hotkeys.action_triggered.disconnect()
    c.hotkeys.action_triggered.connect(recording_handle, Qt.ConnectionType.QueuedConnection)

    yield c
    c.hotkeys.stop()
    c.companion.close()
    c.ui.close()
    c.overlay.close()
    # The guards above raise or record instead of running the live path, and
    # the controller may swallow that raise. No test here intends to capture,
    # generate or error, so anything recorded is a test that silently leaked.
    assert c.captures == []
    assert c.errors == []


def _show_lesson(controller) -> None:
    controller.lesson_steps = [{"title": "t", "explanation": "e"}]
    controller.current_step_index = 0
    controller.input_manager.set_state(TutorState.TEACHING)


def _global_escape(controller, backend, qt_app) -> None:
    """A physical release seen by the hook, then delivered to the GUI thread."""
    backend.deliver("esc", KEY_UP)
    qt_app.processEvents()


def _local_escape(qt_app, widget, auto_repeat: bool = False) -> None:
    qt_app.sendEvent(widget, _escape_event(auto_repeat))


class TestDraftEscape:
    def test_escape_abandons_a_draft_while_idle(self, controller, backend, qt_app):
        controller.hotkeys.start()
        controller.companion.question_input.setText("half a quest")

        _global_escape(controller, backend, qt_app)

        assert controller.companion.question_input.text() == ""
        assert controller.input_manager.current_state is TutorState.IDLE
        assert controller.ui.lbl_status.text() != "Lesson cancelled. Ready."

    def test_escape_abandons_a_draft_and_keeps_the_lesson(self, controller, backend, qt_app):
        controller.hotkeys.start()
        _show_lesson(controller)
        controller.companion.question_input.setText("half a quest")

        _global_escape(controller, backend, qt_app)

        assert controller.companion.question_input.text() == ""
        assert controller.lesson_steps
        assert controller.input_manager.current_state is TutorState.TEACHING

    def test_escape_with_only_focus_clears_arming_and_keeps_the_lesson(
        self, controller, backend, qt_app
    ):
        controller.hotkeys.start()
        _show_lesson(controller)
        controller.field_focused = True
        controller._question_armed_at = 1.0

        _global_escape(controller, backend, qt_app)

        assert controller._question_armed_at is None
        assert controller.input_manager.current_state is TutorState.TEACHING

    def test_escape_while_armed_but_unfocused_is_a_draft_escape(self, controller, backend, qt_app):
        # Windows may refuse to focus the field from a global hook; the press
        # still armed the fast path, and Escape must disarm it rather than
        # dismiss the lesson underneath.
        controller.hotkeys.start()
        _show_lesson(controller)
        controller.field_focused = False
        controller.input_manager.handle_action(InputAction.CAPTURE_SCREEN)
        assert controller._question_armed_at is not None

        _global_escape(controller, backend, qt_app)

        assert controller._question_armed_at is None
        assert controller.input_manager.current_state is TutorState.TEACHING

    def test_next_hotkey_after_a_draft_escape_arms_instead_of_submitting(
        self, controller, backend, qt_app
    ):
        controller.hotkeys.start()
        submitted = []
        controller.companion.question_submitted.connect(submitted.append)
        controller.input_manager.handle_action(InputAction.CAPTURE_SCREEN)
        controller.companion.question_input.setText("half a quest")

        _global_escape(controller, backend, qt_app)
        controller.input_manager.handle_action(InputAction.CAPTURE_SCREEN)

        assert submitted == []
        assert controller.asked == []
        assert controller._question_armed_at is not None

    def test_hotkey_twice_still_submits_within_the_window(self, controller):
        # The existing fast path is untouched: hotkey, hotkey asks.
        submitted = []
        controller.companion.question_submitted.connect(submitted.append)

        controller.input_manager.handle_action(InputAction.CAPTURE_SCREEN)
        controller.input_manager.handle_action(InputAction.CAPTURE_SCREEN)

        assert len(submitted) == 1
        assert controller.asked == submitted


class TestSingleAuthority:
    """One physical Escape produces one effect in either delivery order."""

    def test_local_then_global_acts_once(self, controller, backend, qt_app):
        controller.hotkeys.start()
        _show_lesson(controller)
        controller.companion.question_input.setText("half a quest")

        _local_escape(qt_app, controller.companion.question_input)
        _global_escape(controller, backend, qt_app)

        assert controller.companion.question_input.text() == ""
        assert controller.lesson_steps
        assert controller.handled.count(InputAction.ESCAPE) == 1

    def test_global_then_local_acts_once(self, controller, backend, qt_app):
        controller.hotkeys.start()
        _show_lesson(controller)
        controller.companion.question_input.setText("half a quest")

        _global_escape(controller, backend, qt_app)
        _local_escape(qt_app, controller.companion.question_input)

        assert controller.companion.question_input.text() == ""
        assert controller.lesson_steps
        assert controller.handled.count(InputAction.ESCAPE) == 1

    def test_local_escape_on_the_companion_window_is_ignored_while_hooked(
        self, controller, backend, qt_app
    ):
        controller.hotkeys.start()
        _show_lesson(controller)

        _local_escape(qt_app, controller.companion)

        assert controller.handled == []
        assert controller.lesson_steps

    def test_local_escape_on_the_dev_panel_is_ignored_while_hooked(
        self, controller, backend, qt_app
    ):
        controller.hotkeys.start()
        _show_lesson(controller)

        _local_escape(qt_app, controller.ui)

        assert controller.handled == []
        assert controller.lesson_steps

    def test_held_key_repeats_do_not_reach_the_lesson(self, controller, backend, qt_app):
        controller.hotkeys.start()
        _show_lesson(controller)
        controller.companion.question_input.setText("half a quest")

        # Held Escape: Qt auto-repeats the press, the hook sees KEY_DOWN
        # repeats and a single KEY_UP.
        _local_escape(qt_app, controller.companion.question_input)
        for _ in range(5):
            _local_escape(qt_app, controller.companion.question_input, auto_repeat=True)
            backend.deliver("esc", KEY_DOWN)
        _global_escape(controller, backend, qt_app)

        assert controller.companion.question_input.text() == ""
        assert controller.lesson_steps
        assert controller.handled == [InputAction.ESCAPE]


class TestQtFallback:
    """With no global hook, the Qt key events are the only authority."""

    def test_field_escape_abandons_the_draft(self, controller, qt_app):
        assert not controller.hotkeys.escape_hooked
        _show_lesson(controller)
        controller.companion.question_input.setText("half a quest")

        _local_escape(qt_app, controller.companion.question_input)

        assert controller.companion.question_input.text() == ""
        assert controller.lesson_steps
        assert controller.handled == [InputAction.ESCAPE]

    def test_companion_escape_dismisses_the_lesson(self, controller, qt_app):
        _show_lesson(controller)

        _local_escape(qt_app, controller.companion)

        assert controller.lesson_steps == []
        assert controller.input_manager.current_state is TutorState.IDLE

    def test_dev_panel_escape_cancels_a_demo_once(self, controller, qt_app):
        # The panel used to send CANCEL_LESSON for "any key interrupts the
        # demo" and again for Escape itself.
        controller.demo_manager.is_running = True
        controller.input_manager.set_state(TutorState.TEACHING)

        _local_escape(qt_app, controller.ui)

        assert not controller.demo_manager.is_running
        assert controller.input_manager.current_state is TutorState.IDLE
        assert controller.handled == [InputAction.ESCAPE]

    def test_auto_repeat_on_the_dev_panel_is_ignored(self, controller, qt_app):
        _show_lesson(controller)

        _local_escape(qt_app, controller.ui, auto_repeat=True)

        assert controller.handled == []
        assert controller.lesson_steps

    def test_fallback_engages_after_a_failed_start(self, monkeypatch, qt_app):
        monkeypatch.setattr(hotkeys_module, "keyboard", FakeKeyboard(fail_on="esc"))
        c = DesktopController()
        try:
            assert c.hotkeys.start() is False
            c.lesson_steps = [{"title": "t", "explanation": "e"}]
            c.input_manager.set_state(TutorState.TEACHING)

            _local_escape(qt_app, c.companion)

            assert c.lesson_steps == []
            assert c.input_manager.current_state is TutorState.IDLE
        finally:
            c.companion.close()
            c.ui.close()
            c.overlay.close()

    def test_fallback_engages_after_stop(self, controller, qt_app):
        controller.hotkeys.start()
        controller.hotkeys.stop()
        _show_lesson(controller)

        _local_escape(qt_app, controller.companion)

        assert controller.lesson_steps == []


class TestCancelSemantics:
    def test_escape_outside_composition_cancels_the_lesson(self, controller, backend, qt_app):
        controller.hotkeys.start()
        _show_lesson(controller)

        _global_escape(controller, backend, qt_app)

        assert controller.lesson_steps == []
        assert controller.input_manager.current_state is TutorState.IDLE
        assert controller.ui.lbl_status.text() == "Lesson cancelled. Ready."

    def test_escape_outside_composition_stops_a_demo(self, controller, backend, qt_app):
        controller.hotkeys.start()
        controller.demo_manager.is_running = True
        controller.input_manager.set_state(TutorState.TEACHING)

        _global_escape(controller, backend, qt_app)

        assert not controller.demo_manager.is_running
        assert controller.input_manager.current_state is TutorState.IDLE

    @pytest.mark.parametrize("state", [TutorState.CAPTURING, TutorState.ANALYZING])
    def test_escape_while_busy_cancels_the_visible_operation(
        self, controller, backend, qt_app, state
    ):
        controller.hotkeys.start()
        controller.input_manager.set_state(state)

        _global_escape(controller, backend, qt_app)

        assert controller.input_manager.current_state is TutorState.IDLE

    @pytest.mark.parametrize("state", [TutorState.CAPTURING, TutorState.ANALYZING])
    def test_escape_while_busy_cancels_despite_a_stale_hidden_draft(
        self, controller, backend, qt_app, state
    ):
        # A question submitted from the developer panel can leave the
        # companion hidden with old text, focus and arming. The first Escape
        # must cancel the busy operation, not tidy the draft nobody can see.
        controller.hotkeys.start()
        controller.companion.question_input.setText("stale draft")
        controller.field_focused = True
        controller._question_armed_at = 1.0
        controller.input_manager.set_state(state)

        _global_escape(controller, backend, qt_app)

        assert controller.input_manager.current_state is TutorState.IDLE
        assert controller.ui.lbl_status.text() == "Lesson cancelled. Ready."
        assert controller.companion.question_input.text() == "stale draft"
        assert controller._question_armed_at is None

    @pytest.mark.parametrize("state", [TutorState.CAPTURING, TutorState.ANALYZING])
    def test_qt_fallback_escape_while_busy_cancels_despite_a_stale_draft(
        self, controller, qt_app, state
    ):
        assert not controller.hotkeys.escape_hooked
        controller.companion.question_input.setText("stale draft")
        controller.input_manager.set_state(state)

        _local_escape(qt_app, controller.companion.question_input)

        assert controller.input_manager.current_state is TutorState.IDLE
        assert controller.handled == [InputAction.ESCAPE]

    def test_escape_while_finished_still_abandons_the_draft(self, controller, backend, qt_app):
        controller.hotkeys.start()
        controller.input_manager.set_state(TutorState.FINISHED)
        controller.companion.question_input.setText("half a quest")

        _global_escape(controller, backend, qt_app)

        assert controller.companion.question_input.text() == ""
        assert controller.input_manager.current_state is TutorState.FINISHED

    def test_escape_while_idle_with_nothing_drafted_does_nothing(self, controller, backend, qt_app):
        controller.hotkeys.start()
        status = controller.ui.lbl_status.text()

        _global_escape(controller, backend, qt_app)

        assert controller.input_manager.current_state is TutorState.IDLE
        assert controller.ui.lbl_status.text() == status

    def test_explicit_cancel_ignores_the_draft_and_cancels(self, controller):
        _show_lesson(controller)
        controller.companion.question_input.setText("half a quest")

        controller.input_manager.handle_action(InputAction.CANCEL_LESSON)

        assert controller.lesson_steps == []
        assert controller.input_manager.current_state is TutorState.IDLE

    def test_explicit_cancel_still_stops_a_demo(self, controller):
        controller.demo_manager.is_running = True
        controller.input_manager.set_state(TutorState.TEACHING)

        controller.input_manager.handle_action(InputAction.CANCEL_LESSON)

        assert not controller.demo_manager.is_running
