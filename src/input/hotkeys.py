import logging

import keyboard
from PyQt6.QtCore import QObject, pyqtSignal

from src.input.events import InputAction

logger = logging.getLogger(__name__)


class HotkeyManager(QObject):
    # Emitted when a hotkey is pressed. This crosses from the keyboard
    # background thread into the PyQt main thread safely.
    action_triggered = pyqtSignal(InputAction)

    def __init__(self):
        super().__init__()
        self._running = False

    def _trigger(self, action: InputAction):
        self.action_triggered.emit(action)

    def start(self):
        if self._running:
            return

        try:
            keyboard.add_hotkey("ctrl+shift+a", lambda: self._trigger(InputAction.CAPTURE_SCREEN))
            keyboard.add_hotkey("ctrl+shift+d", lambda: self._trigger(InputAction.TOGGLE_DEBUG))
            keyboard.add_hotkey("esc", lambda: self._trigger(InputAction.CANCEL_LESSON))

            self._running = True
            logger.info("Global hotkeys registered (Ctrl+Shift+A, Ctrl+Shift+D, Esc)")
        except Exception as e:
            logger.error("Failed to register global hotkeys: %s", e)
            logger.warning("Make sure you are running as Administrator (Windows) or root (Linux)")

    def stop(self):
        if not self._running:
            return

        try:
            keyboard.unhook_all_hotkeys()
            self._running = False
            logger.info("Global hotkeys unregistered")
        except Exception as e:
            logger.error("Failed to unregister global hotkeys: %s", e)
