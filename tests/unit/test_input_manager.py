"""Unit tests for action routing and the state guards around it.

No QApplication and no display are needed: InputManager is a bare QObject and
dispatch is plain Python.
"""

import pytest

from src.input.events import InputAction
from src.input.input_manager import InputManager
from src.input.state_machine import TutorState


@pytest.fixture
def manager():
    """An InputManager that records every action it dispatches."""
    manager = InputManager()
    manager.dispatched = []
    manager.add_listener(manager.dispatched.append)
    return manager


class TestCaptureGuard:
    @pytest.mark.parametrize(
        "state",
        [TutorState.IDLE, TutorState.TEACHING, TutorState.FINISHED],
    )
    def test_capture_is_allowed_when_not_busy(self, manager, state):
        # TEACHING and FINISHED matter: pressing the hotkey again while a
        # lesson is on screen must start a new one rather than be swallowed.
        manager.set_state(state)

        manager.handle_action(InputAction.CAPTURE_SCREEN)

        assert manager.dispatched == [InputAction.CAPTURE_SCREEN]

    @pytest.mark.parametrize("state", [TutorState.CAPTURING, TutorState.ANALYZING])
    def test_capture_is_blocked_while_genuinely_busy(self, manager, state):
        manager.set_state(state)

        manager.handle_action(InputAction.CAPTURE_SCREEN)

        assert manager.dispatched == []

    @pytest.mark.parametrize(
        "state",
        [TutorState.IDLE, TutorState.TEACHING, TutorState.FINISHED],
    )
    def test_ask_is_allowed_when_not_busy(self, manager, state):
        # Asking from the companion's field replaces the old modal dialog, so
        # it has exactly the capture's guard: a typed question during a
        # lesson starts a new one.
        manager.set_state(state)

        manager.handle_action(InputAction.ASK)

        assert manager.dispatched == [InputAction.ASK]

    @pytest.mark.parametrize("state", [TutorState.CAPTURING, TutorState.ANALYZING])
    def test_ask_is_blocked_while_genuinely_busy(self, manager, state):
        manager.set_state(state)

        manager.handle_action(InputAction.ASK)

        assert manager.dispatched == []


class TestCancelGuard:
    def test_cancel_is_dropped_when_idle(self, manager):
        manager.handle_action(InputAction.CANCEL_LESSON)

        assert manager.dispatched == []

    @pytest.mark.parametrize(
        "state",
        [TutorState.CAPTURING, TutorState.ANALYZING, TutorState.TEACHING],
    )
    def test_cancel_reaches_listeners_whenever_something_is_running(self, manager, state):
        # A demo sets TEACHING; while it incorrectly stayed IDLE, Esc was
        # dropped here and demos could not be interrupted at all.
        manager.set_state(state)

        manager.handle_action(InputAction.CANCEL_LESSON)

        assert manager.dispatched == [InputAction.CANCEL_LESSON]


class TestEscapeGuard:
    @pytest.mark.parametrize("state", list(TutorState))
    def test_escape_reaches_listeners_in_every_state(self, manager, state):
        # A question can be drafted while IDLE, and only the listener can see
        # the draft, so the manager cannot decide here whether Escape means
        # "abandon the draft" or "cancel the lesson".
        manager.set_state(state)

        manager.handle_action(InputAction.ESCAPE)

        assert manager.dispatched == [InputAction.ESCAPE]

    def test_escape_is_not_translated_into_cancel(self, manager):
        manager.set_state(TutorState.TEACHING)

        manager.handle_action(InputAction.ESCAPE)

        assert InputAction.CANCEL_LESSON not in manager.dispatched


class TestNavigationGuard:
    @pytest.mark.parametrize("action", [InputAction.NEXT_STEP, InputAction.PREV_STEP])
    def test_navigation_requires_a_lesson_on_screen(self, manager, action):
        manager.set_state(TutorState.TEACHING)

        manager.handle_action(action)

        assert manager.dispatched == [action]

    @pytest.mark.parametrize("action", [InputAction.NEXT_STEP, InputAction.PREV_STEP])
    def test_navigation_is_ignored_while_idle(self, manager, action):
        manager.handle_action(action)

        assert manager.dispatched == []


class TestDebugToggle:
    @pytest.mark.parametrize("state", [s for s in TutorState if s is not TutorState.SELECTING])
    def test_debug_toggle_is_always_available(self, manager, state):
        manager.set_state(state)

        manager.handle_action(InputAction.TOGGLE_DEBUG)

        assert manager.dispatched == [InputAction.TOGGLE_DEBUG]

    def test_debug_toggle_is_blocked_while_selecting(self, manager):
        manager.set_state(TutorState.SELECTING)

        manager.handle_action(InputAction.TOGGLE_DEBUG)

        assert manager.dispatched == []


class TestStateTracking:
    def test_starts_idle(self):
        assert InputManager().current_state is TutorState.IDLE

    def test_set_state_updates_current_state(self, manager):
        manager.set_state(TutorState.ANALYZING)

        assert manager.current_state is TutorState.ANALYZING

    def test_every_listener_receives_the_action(self):
        manager = InputManager()
        first, second = [], []
        manager.add_listener(first.append)
        manager.add_listener(second.append)

        manager.handle_action(InputAction.TOGGLE_DEBUG)

        assert first == [InputAction.TOGGLE_DEBUG]
        assert second == [InputAction.TOGGLE_DEBUG]


class TestThreadAffinity:
    def test_is_a_qobject_so_signals_can_queue_to_the_gui_thread(self):
        # Global hotkeys arrive on the keyboard library's listener thread.
        # Qt picks queued-versus-direct delivery from the receiver's thread
        # affinity, which a plain Python object does not have -- the handler
        # would then run on the hotkey thread and touch Qt widgets there.
        from PyQt6.QtCore import QObject

        assert isinstance(InputManager(), QObject)
