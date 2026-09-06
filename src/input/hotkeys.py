import logging
from collections.abc import Callable
from typing import Any

import keyboard
from PyQt6.QtCore import QObject, pyqtSignal

from src.input.events import InputAction

logger = logging.getLogger(__name__)

# Chorded hotkeys, registered through keyboard.add_hotkey.
_CHORDS: tuple[tuple[str, InputAction], ...] = (
    ("ctrl+shift+a", InputAction.CAPTURE_SCREEN),
    ("ctrl+shift+s", InputAction.SELECT_REGION),
    ("ctrl+shift+d", InputAction.TOGGLE_DEBUG),
)

_ESCAPE_KEY = "esc"


class HotkeyManager(QObject):
    """Registers the global hotkeys and reports which of them actually took.

    Escape is hooked per key on KEY_UP rather than as a hotkey. A held key
    repeats KEY_DOWN, so a release hook fires once per physical press without
    any time-based deduplication. add_hotkey(trigger_on_release=True) is not
    an option: in the installed keyboard source, non-blocking hotkey dispatch
    reads _pressed_events after KEY_UP has already removed the key, so the
    release never matches. Per-key hooks dispatch on event.scan_code and do
    not have that problem.

    Callbacks run on keyboard's listener thread. They only emit the signal;
    nothing here touches a Qt widget.
    """

    # Emitted when a hotkey is pressed. This crosses from the keyboard
    # background thread into the PyQt main thread safely.
    action_triggered = pyqtSignal(InputAction)

    def __init__(self, backend: Any = None) -> None:
        """
        Args:
            backend: The keyboard module or a stand-in with the same
                add_hotkey / hook_key / remove_hotkey / unhook surface. Tests
                pass a fake so no real hook is ever installed.
        """
        super().__init__()
        self._backend = backend if backend is not None else keyboard
        # Undo callables for every registration that succeeded, so a partial
        # start can be rolled back and stop removes exactly what was added.
        self._removers: list[Callable[[], None]] = []
        self._escape_hooked = False
        # Identity of the current start() run. Every callback carries the run
        # it was registered under and only acts while that run is current, so
        # a callback the backend failed to remove stays inert after stop and
        # after a later start installs its replacement. A shared boolean could
        # not do this: it becomes true again on restart.
        self._run: object | None = None

    @property
    def is_running(self) -> bool:
        return bool(self._removers)

    @property
    def escape_hooked(self) -> bool:
        """True only while the global Escape release hook is installed.

        The controller uses this to pick one authority for a physical Escape:
        the hook when it is in place, the Qt key events otherwise.
        """
        return self._escape_hooked

    def _trigger(self, run: object, action: InputAction) -> None:
        if run is not self._run:
            return
        self.action_triggered.emit(action)

    def _on_escape_event(self, run: object, event: Any) -> None:
        """Per-key hook callback; ignores the KEY_DOWN repeats of a held key."""
        if getattr(event, "event_type", None) != keyboard.KEY_UP:
            return
        self._trigger(run, InputAction.ESCAPE)

    def start(self) -> bool:
        """Registers every hotkey, or none of them.

        Returns:
            True when all hooks are installed. On any failure the hooks that
            did register are removed again, so the local Qt fallback for
            Escape is not shadowed by a half-installed global one.
        """
        if self.is_running:
            return True

        run = object()
        self._run = run
        try:
            for chord, action in _CHORDS:
                handle = self._backend.add_hotkey(
                    chord, lambda action=action: self._trigger(run, action)
                )
                self._removers.append(lambda handle=handle: self._backend.remove_hotkey(handle))

            handle = self._backend.hook_key(
                _ESCAPE_KEY, lambda event: self._on_escape_event(run, event)
            )
            self._removers.append(lambda handle=handle: self._backend.unhook(handle))
            self._escape_hooked = True
        except Exception as e:
            logger.error("Failed to register global hotkeys: %s", e)
            logger.warning("Make sure you are running as Administrator (Windows) or root (Linux)")
            self._remove_all()
            return False

        logger.info("Global hotkeys registered (Ctrl+Shift+A, Ctrl+Shift+S, Ctrl+Shift+D, Esc)")
        return True

    def stop(self) -> bool:
        """Retires the current registrations.

        Returns:
            True when the backend removed every hook. False when at least one
            removal raised: that hook may still be installed in the OS, but
            its callback is retired and will not emit again.
        """
        if not self.is_running:
            return True
        if not self._remove_all():
            logger.error("Global hotkeys retired, but at least one OS hook may still be installed")
            return False
        logger.info("Global hotkeys unregistered")
        return True

    def _remove_all(self) -> bool:
        # The run is retired and Escape reported unhooked before touching the
        # backend: from this point every callback of this run is inert and the
        # Qt fallback is the authority, even if a removal fails.
        self._run = None
        self._escape_hooked = False
        removers, self._removers = self._removers, []
        all_removed = True
        for remove in reversed(removers):
            try:
                remove()
            except Exception as e:
                all_removed = False
                logger.error("Failed to unregister a global hotkey: %s", e)
        return all_removed
