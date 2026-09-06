"""Local spoken narration for lesson steps.

The controller owns one NarrationService. It speaks at most one utterance at
a time, and each utterance gets a *fresh* backend engine whose callbacks are
bound to that utterance alone. Qt's QTextToSpeech reports state changes with
no per-utterance identity, so reusing one engine would mean guessing which
utterance a late "Ready" belongs to; a retired engine's signals are simply
disconnected and ignored instead.

Everything here is local: on Windows the voice is SAPI through Qt's text-to-
speech module. Anywhere that is missing -- WSL, CI, a Windows install
without the module -- the service reports itself unavailable and the app is
text-only. No cloud voice, no mock engine, no audio daemon is ever started.
"""

from __future__ import annotations

import logging
import sys
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Protocol

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

logger = logging.getLogger(__name__)

# Backend state, normalised so the service does not depend on Qt's enum.
ENGINE_READY = "ready"
ENGINE_BUSY = "busy"
ENGINE_ERROR = "error"

# QTextToSpeech::State names. Synthesizing and Paused both mean an utterance
# is in progress and has not ended; only Ready after a say() means it has.
_QT_STATE_NAMES = {
    "Ready": ENGINE_READY,
    "Speaking": ENGINE_BUSY,
    "Synthesizing": ENGINE_BUSY,
    "Paused": ENGINE_BUSY,
    "Error": ENGINE_ERROR,
}

SAPI_ENGINE_NAME = "sapi"


class NarrationUnavailableError(RuntimeError):
    """No local speech engine can be created on this machine."""


def normalize_state(state: Any) -> str:
    """Maps a QTextToSpeech state to ENGINE_READY, ENGINE_BUSY or ENGINE_ERROR.

    An unknown state is treated as busy: the service then waits for a Ready
    it can trust rather than declaring an utterance finished by mistake.
    """
    name = getattr(state, "name", None) or str(state)
    normalized = _QT_STATE_NAMES.get(name)
    if normalized is None:
        logger.warning("Unknown text-to-speech state %r; treating it as busy.", state)
        return ENGINE_BUSY
    return normalized


@dataclass(frozen=True)
class NarrationToken:
    """Identifies one utterance: which lesson and step it reads, and when.

    ``generation`` increases with every utterance the service starts, so a
    replay of the same step is a different token from the first reading.
    """

    lesson_id: int
    step_index: int
    generation: int


class UtteranceEngine(QObject):
    """One backend voice object, used for exactly one utterance.

    Subclasses wrap a real engine or a test fake. State is reported through
    ``state_changed`` in the normalised vocabulary above; ``error_occurred``
    carries a human-readable message. Both may fire synchronously from
    inside ``say()`` or ``stop()``.
    """

    state_changed = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def state(self) -> str:
        raise NotImplementedError

    def say(self, text: str) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def error_string(self) -> str:
        return ""

    def dispose(self) -> None:
        """Releases the backend object. The engine is never used again."""
        self.deleteLater()


class EngineFactory(Protocol):
    @property
    def available(self) -> bool: ...

    @property
    def unavailable_reason(self) -> str: ...

    def create(self) -> UtteranceEngine: ...


class QtSpeechEngine(UtteranceEngine):
    """A QTextToSpeech object wrapped for one utterance."""

    def __init__(self, tts: Any) -> None:
        super().__init__()
        self._tts = tts
        tts.stateChanged.connect(self._relay_state)
        tts.errorOccurred.connect(self._relay_error)

    def _relay_state(self, state: Any) -> None:
        self.state_changed.emit(normalize_state(state))

    def _relay_error(self, reason: Any, message: str) -> None:
        self.error_occurred.emit(message or str(reason))

    def state(self) -> str:
        return normalize_state(self._tts.state())

    def say(self, text: str) -> None:
        self._tts.say(text)

    def stop(self) -> None:
        self._tts.stop(type(self._tts).BoundaryHint.Immediate)

    def error_string(self) -> str:
        return str(self._tts.errorString())

    def dispose(self) -> None:
        for signal in (self._tts.stateChanged, self._tts.errorOccurred):
            with suppress(TypeError):
                signal.disconnect()
        self._tts.deleteLater()
        super().dispose()


class QtSpeechEngineFactory:
    """Creates SAPI engines through PyQt6.QtTextToSpeech, or explains why not.

    The probe only lists engines; it constructs nothing until an utterance
    needs a voice, so an unavailable machine pays nothing at startup.
    """

    def __init__(self, platform: str = sys.platform) -> None:
        self._tts_class: Any = None
        self._reason = self._probe(platform)

    def _probe(self, platform: str) -> str:
        if platform != "win32":
            return "Voice needs Windows speech (SAPI)."
        try:
            from PyQt6.QtTextToSpeech import QTextToSpeech
        except ImportError:
            return "Qt text-to-speech is not installed."
        try:
            engines = [str(name) for name in QTextToSpeech.availableEngines()]
        except Exception as exc:  # a broken plugin must not take the app down
            logger.warning("Could not list text-to-speech engines: %s", exc)
            return "Speech engines could not be listed."
        if SAPI_ENGINE_NAME not in engines:
            return "Windows SAPI speech is not available."
        self._tts_class = QTextToSpeech
        return ""

    @property
    def available(self) -> bool:
        return not self._reason

    @property
    def unavailable_reason(self) -> str:
        return self._reason

    def create(self) -> UtteranceEngine:
        if self._tts_class is None:
            raise NarrationUnavailableError(self._reason)
        return QtSpeechEngine(self._tts_class(SAPI_ENGINE_NAME))


def default_engine_factory() -> EngineFactory:
    """The factory a controller uses when none is injected."""
    return QtSpeechEngineFactory()


def _error_message(engine: UtteranceEngine, fallback: str) -> str:
    """The engine's error text, or ``fallback`` when it is blank or unobtainable.

    Asked of an engine already in its error state, from a timer or signal
    slot; a backend that cannot even describe its failure must still be
    retired and reported rather than raise there.
    """
    try:
        message = engine.error_string()
    except Exception as exc:
        logger.warning("Speech engine could not report its error: %s", exc)
        return fallback
    return message or fallback


class _Utterance:
    __slots__ = ("engine", "started", "text", "token")

    def __init__(self, token: NarrationToken, text: str) -> None:
        self.token = token
        self.text = text
        # None until the deferred start creates the backend object. A stop
        # that lands before then has nothing to tell and simply retires it.
        self.engine: UtteranceEngine | None = None
        # True once say() has been issued. A Ready before that is the engine
        # finishing initialisation; a Ready after it is the utterance ending.
        self.started = False


class NarrationService(QObject):
    """Speaks one piece of text at a time through a local engine.

    Signals carry the token of the utterance they concern. Nothing is emitted
    for a stop: the caller asked for it and reads the service's state.
    """

    speech_started = pyqtSignal(object)
    speech_finished = pyqtSignal(object)
    speech_failed = pyqtSignal(object, str)

    def __init__(self, factory: EngineFactory | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._factory = factory if factory is not None else default_engine_factory()
        self._enabled = self._factory.available
        self._generation = 0
        self._current: _Utterance | None = None
        self._shut_down = False
        if not self._factory.available:
            logger.info("Voice unavailable: %s", self._factory.unavailable_reason)

    # -- state -------------------------------------------------------------

    @property
    def available(self) -> bool:
        return self._factory.available and not self._shut_down

    @property
    def unavailable_reason(self) -> str:
        return self._factory.unavailable_reason

    @property
    def enabled(self) -> bool:
        return self._enabled and self.available

    @property
    def current_token(self) -> NarrationToken | None:
        return self._current.token if self._current is not None else None

    def is_speaking(self) -> bool:
        """True while an utterance is current, including one still initialising."""
        return self._current is not None

    def set_enabled(self, enabled: bool) -> None:
        """Turns the voice on or off. Off stops playback; on starts nothing."""
        if not self.available:
            enabled = False
        self._enabled = enabled
        if not enabled:
            self.stop()

    # -- speaking ----------------------------------------------------------

    def speak(self, text: str, *, lesson_id: int, step_index: int) -> NarrationToken | None:
        """Replaces whatever is playing with ``text``.

        Returns the accepted utterance's token, or None when the voice is off
        or unavailable or the text is blank. The engine is created and spoken
        to on the next event-loop turn, so the caller holds the token before
        any ``speech_*`` signal for it can fire, including a creation failure
        or an utterance so short it completes inside ``say()``. A stop before
        that turn retires the utterance and nothing is created.
        """
        if not self.enabled:
            return None
        text = (text or "").strip()
        if not text:
            return None
        self.stop()

        self._generation += 1
        utterance = _Utterance(NarrationToken(lesson_id, step_index, self._generation), text)
        self._current = utterance
        QTimer.singleShot(0, lambda: self._start(utterance))
        return utterance.token

    def stop(self) -> None:
        """Stops the current utterance, if any. Safe to call at any time."""
        utterance = self._current
        if utterance is None:
            return
        # Retired before the backend is told: stop() may emit state changes
        # synchronously, and those belong to an utterance that is over.
        self._retire(utterance)
        self._silence(utterance)

    def shutdown(self) -> None:
        """Stops playback for good; every later call and callback is ignored."""
        self._shut_down = True
        self.stop()

    # -- utterance lifecycle -----------------------------------------------

    def _start(self, utterance: _Utterance) -> None:
        """The deferred half of speak(): creates the engine and speaks to it.

        Runs from a timer slot, so nothing may escape: an exception here
        would abort the process rather than reach a caller.
        """
        if not self._is_live(utterance) or utterance.engine is not None:
            return
        try:
            engine = self._factory.create()
        except Exception as exc:
            logger.warning("Could not create a speech engine: %s", exc)
            self._fail(utterance, str(exc))
            return
        utterance.engine = engine
        engine.state_changed.connect(lambda state: self._on_engine_state(utterance, state))
        engine.error_occurred.connect(lambda message: self._on_engine_error(utterance, message))

        try:
            state = engine.state()
        except Exception as exc:
            self._fail(utterance, str(exc))
            return
        if state == ENGINE_ERROR:
            self._fail(utterance, _error_message(engine, "speech engine failed to start"))
        elif state == ENGINE_READY:
            self._begin(utterance)
        # Otherwise the engine is still initialising; it is spoken to when it
        # reports Ready, unless it has been retired by then.

    def _begin(self, utterance: _Utterance) -> None:
        utterance.started = True
        # Started is reported before say(): a tiny utterance may run to Ready
        # synchronously inside say(), and finished must not precede started.
        self.speech_started.emit(utterance.token)
        # A started listener may have stopped, switched off, shut down or
        # replaced this utterance; a retired engine is never spoken to.
        if not self._is_live(utterance) or utterance.engine is None:
            return
        try:
            utterance.engine.say(utterance.text)
        except Exception as exc:
            if self._is_live(utterance):
                self._fail(utterance, str(exc))

    def _retire(self, utterance: _Utterance) -> None:
        if self._current is utterance:
            self._current = None
        engine = utterance.engine
        if engine is None:
            return
        # Each signal is looked up and disconnected on its own: a deleted Qt
        # object raises RuntimeError on the attribute access itself, and one
        # failure must not skip the other signal or the stop/dispose after.
        for name in ("state_changed", "error_occurred"):
            try:
                getattr(engine, name).disconnect()
            except TypeError:
                pass  # nothing connected
            except Exception as exc:
                logger.warning("Could not disconnect speech engine %s: %s", name, exc)

    def _silence(self, utterance: _Utterance) -> None:
        """Stops and releases a retired utterance's engine. Never raises.

        Called from UI callbacks and Qt slots, where an exception from a dying
        backend would be far worse than a leaked voice object.
        """
        engine = utterance.engine
        if engine is None:
            return
        try:
            engine.stop()
        except Exception as exc:  # the voice is already gone as far as we care
            logger.warning("Speech engine stop failed: %s", exc)
        try:
            engine.dispose()
        except Exception as exc:
            logger.warning("Speech engine dispose failed: %s", exc)

    def _fail(self, utterance: _Utterance, message: str) -> None:
        logger.warning("Speech failed for %s: %s", utterance.token, message)
        self._retire(utterance)
        # Stopped explicitly: an error signal need not mean playback ended,
        # and deferred deletion alone would let it run on for a while.
        self._silence(utterance)
        self.speech_failed.emit(utterance.token, message)

    def _is_live(self, utterance: _Utterance) -> bool:
        return not self._shut_down and self._current is utterance

    def _on_engine_state(self, utterance: _Utterance, state: str) -> None:
        if not self._is_live(utterance):
            return
        engine = utterance.engine
        if engine is None:  # signals are only connected once an engine exists
            return
        if state == ENGINE_ERROR:
            self._fail(utterance, _error_message(engine, "speech engine error"))
            return
        if not utterance.started:
            if state == ENGINE_READY:
                self._begin(utterance)
            return
        if state == ENGINE_READY:
            self._retire(utterance)
            try:
                engine.dispose()
            except Exception as exc:
                logger.warning("Speech engine dispose failed: %s", exc)
            self.speech_finished.emit(utterance.token)

    def _on_engine_error(self, utterance: _Utterance, message: str) -> None:
        if not self._is_live(utterance):
            return
        self._fail(utterance, message or "speech engine error")
