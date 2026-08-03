import logging
from collections.abc import Callable

from PyQt6.QtCore import QObject, pyqtSignal

from src.input.events import InputAction
from src.input.state_machine import TutorState

logger = logging.getLogger(__name__)

# Starting a new capture is allowed while a lesson is on screen: a second
# hotkey press should replace the current lesson rather than be swallowed.
# CAPTURING and ANALYZING are the only genuinely busy states.
_CAPTURE_READY_STATES = frozenset({TutorState.IDLE, TutorState.TEACHING, TutorState.FINISHED})

# Step navigation only means anything while a lesson is displayed.
_NAVIGABLE_STATES = frozenset({TutorState.TEACHING, TutorState.FINISHED})


class InputManager(QObject):
    """Routes user actions to listeners, guarded by the tutor's state.

    Subclasses QObject for a specific reason. Global hotkeys are delivered on
    the `keyboard` library's listener thread. Qt decides queued-versus-direct
    delivery from the *receiver's* thread affinity, and a plain Python callable
    has none, so the connection resolves as direct and the handler runs on the
    hotkey thread. Everything downstream of here touches Qt widgets, including
    QInputDialog and QMessageBox. As a QObject constructed on the GUI thread,
    this class gives the connection a receiver with main-thread affinity.
    """

    # Emitted after the state actually changes. The companion widget renders
    # from this rather than the controller pushing UI updates from each call
    # site, which is what kept the old panel and the real state out of sync.
    state_changed = pyqtSignal(TutorState)

    def __init__(self) -> None:
        super().__init__()
        self.current_state = TutorState.IDLE
        self.listeners: list[Callable[[InputAction], None]] = []

    def set_state(self, new_state: TutorState) -> None:
        if self.current_state != new_state:
            logger.info(
                "TutorState transition: %s -> %s",
                self.current_state.name,
                new_state.name,
            )
            self.current_state = new_state
            self.state_changed.emit(new_state)

    def add_listener(self, callback: Callable[[InputAction], None]) -> None:
        self.listeners.append(callback)

    def handle_action(self, action: InputAction) -> None:
        logger.info(
            "InputManager received action: %s (Current State: %s)",
            action.name,
            self.current_state.name,
        )

        if action == InputAction.CAPTURE_SCREEN:
            if self.current_state in _CAPTURE_READY_STATES:
                self._dispatch(action)
            else:
                logger.warning(
                    "Ignored CAPTURE_SCREEN: Tutor is busy (%s)",
                    self.current_state.name,
                )

        elif action == InputAction.CANCEL_LESSON:
            if self.current_state != TutorState.IDLE:
                self._dispatch(action)

        elif action in (InputAction.NEXT_STEP, InputAction.PREV_STEP):
            if self.current_state in _NAVIGABLE_STATES:
                self._dispatch(action)
            else:
                logger.debug(
                    "Ignored %s: no lesson on screen (%s)",
                    action.name,
                    self.current_state.name,
                )

        elif action == InputAction.TOGGLE_DEBUG:
            self._dispatch(action)

    def _dispatch(self, action: InputAction) -> None:
        for listener in self.listeners:
            listener(action)
