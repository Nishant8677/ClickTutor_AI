"""NarrationService against a fake engine: one utterance, token-bound callbacks."""

from __future__ import annotations

from enum import Enum
from unittest.mock import Mock

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from src.desktop.narration import (
    ENGINE_BUSY,
    ENGINE_ERROR,
    ENGINE_READY,
    NarrationService,
    UtteranceEngine,
    normalize_state,
)


class FakeEngine(UtteranceEngine):
    """A backend the test drives. Nothing plays."""

    def __init__(self, initial: str = ENGINE_READY, on_say=None, on_stop=None) -> None:
        super().__init__()
        self._state = initial
        self._error = ""
        self.spoken: list[str] = []
        self.stopped = 0
        self.disposed = False
        self._on_say = on_say
        self._on_stop = on_stop

    def state(self) -> str:
        return self._state

    def say(self, text: str) -> None:
        self.spoken.append(text)
        if self._on_say:
            self._on_say()

    def stop(self) -> None:
        self.stopped += 1
        if self._on_stop:
            self._on_stop()
        # SAPI reports Ready after a stop, synchronously as far as we know.
        self.set_state(ENGINE_READY)

    def error_string(self) -> str:
        return self._error

    def dispose(self) -> None:
        self.disposed = True

    def set_state(self, state: str) -> None:
        self._state = state
        self.state_changed.emit(state)

    def fail(self, message: str) -> None:
        self._error = message
        self.error_occurred.emit(message)
        self.set_state(ENGINE_ERROR)

    def finish(self) -> None:
        self.set_state(ENGINE_BUSY)
        self.set_state(ENGINE_READY)


class FakeFactory:
    def __init__(self, available: bool = True, initial: str = ENGINE_READY) -> None:
        self.available = available
        self.unavailable_reason = "" if available else "no voice here"
        self.initial = initial
        self.engines: list[FakeEngine] = []
        self.on_say = None
        self.on_stop = None
        self.raise_on_create: Exception | None = None

    def create(self) -> FakeEngine:
        if self.raise_on_create is not None:
            raise self.raise_on_create
        engine = FakeEngine(self.initial, self.on_say, self.on_stop)
        self.engines.append(engine)
        return engine


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def factory():
    return FakeFactory()


@pytest.fixture
def service(qt_app, factory):
    s = NarrationService(factory=factory)
    s.events = []
    s.speech_started.connect(lambda t: s.events.append(("started", t)))
    s.speech_finished.connect(lambda t: s.events.append(("finished", t)))
    s.speech_failed.connect(lambda t, m: s.events.append(("failed", t, m)))
    return s


def pump():
    """Runs the deferred start speak() posted, and anything queued behind it."""
    QApplication.sendPostedEvents()
    QApplication.processEvents()


def speak(service, text="hello", lesson=1, step=0, start=True):
    """Requests an utterance; by default lets its deferred start run too."""
    token = service.speak(text, lesson_id=lesson, step_index=step)
    if start:
        pump()
    return token


class TestOneUtterance:
    def test_ready_engine_is_spoken_to_at_once_and_finishes_on_ready(self, service, factory):
        token = speak(service)

        engine = factory.engines[0]
        assert engine.spoken == ["hello"]
        assert service.is_speaking() and service.current_token == token
        assert service.events == [("started", token)]

        engine.finish()

        assert not service.is_speaking()
        assert service.events[-1] == ("finished", token)
        assert engine.disposed

    def test_token_is_held_before_any_callback_and_survives_synchronous_completion(
        self, service, factory
    ):
        # say() runs the whole utterance to Ready before returning, as a tiny
        # SAPI utterance can. The caller still gets the token it was told.
        factory.on_say = lambda: factory.engines[-1].set_state(ENGINE_READY)
        token = speak(service, start=False)

        assert token is not None and service.current_token == token
        assert factory.engines == [] and service.events == []

        pump()

        assert service.events == [("started", token), ("finished", token)]
        assert not service.is_speaking()

    def test_stop_before_the_deferred_start_creates_nothing(self, service, factory):
        speak(service, start=False)
        service.stop()
        pump()
        assert factory.engines == [] and service.events == []
        assert not service.is_speaking()

    def test_a_second_pump_does_not_start_the_utterance_twice(self, service, factory):
        speak(service)
        engine = factory.engines[0]
        pump()
        assert len(factory.engines) == 1 and engine.spoken == ["hello"]

    def test_initial_ready_is_not_completion(self, service, factory):
        # The engine reports Ready when constructed; that must not be taken
        # as the utterance ending before say() has even been issued.
        speak(service)
        assert service.is_speaking()
        assert not any(kind == "finished" for kind, *_ in service.events)

    def test_delayed_initialisation_speaks_only_once_ready(self, service, factory):
        factory.initial = "initialising"
        token = speak(service)
        engine = factory.engines[0]
        assert engine.spoken == [] and token is not None and service.is_speaking()

        engine.set_state(ENGINE_READY)
        assert engine.spoken == ["hello"]
        engine.finish()
        assert service.events[-1] == ("finished", token)

    def test_delayed_initialisation_cancelled_before_start_never_speaks(self, service, factory):
        factory.initial = "initialising"
        speak(service)
        engine = factory.engines[0]

        service.stop()
        engine.set_state(ENGINE_READY)

        assert engine.spoken == []
        assert engine.disposed
        assert not any(kind == "finished" for kind, *_ in service.events)

    def test_blank_text_starts_nothing(self, service, factory):
        assert speak(service, "   ") is None
        assert factory.engines == []

    def test_tokens_increase_and_carry_lesson_and_step(self, service):
        first = speak(service, lesson=3, step=2)
        second = speak(service, lesson=3, step=2)
        assert (first.lesson_id, first.step_index) == (3, 2)
        assert second.generation > first.generation


class TestReplacementAndStop:
    def test_replacement_retires_the_old_engine_before_stopping_it(self, service, factory):
        seen = []
        factory.on_stop = lambda: seen.append(service.current_token)
        first = speak(service, "one")
        second = speak(service, "two")

        old, new = factory.engines
        assert old.stopped == 1 and old.disposed
        assert seen == [None], "current was cleared before the backend stop"
        assert new.spoken == ["two"]
        assert service.current_token == second
        # The Ready the old engine emitted from stop() was not a completion.
        assert ("finished", first) not in service.events

    def test_stop_keeps_no_current_and_emits_nothing(self, service, factory):
        speak(service)
        service.stop()
        assert not service.is_speaking()
        assert [kind for kind, *_ in service.events] == ["started"]
        service.stop()  # idempotent

    def test_synchronous_late_events_from_a_retired_engine_are_ignored(self, service, factory):
        speak(service, "one")
        old = factory.engines[0]
        speak(service, "two")

        old.finish()
        old.fail("late")

        assert service.is_speaking()
        assert service.current_token.generation == 2
        assert [kind for kind, *_ in service.events] == ["started", "started"]

    def test_queued_late_events_from_a_retired_engine_are_ignored(self, service, factory):
        speak(service, "one")
        old = factory.engines[0]
        QTimer.singleShot(0, old.finish)
        service.stop()
        speak(service, "two")

        QApplication.processEvents()

        assert service.is_speaking()
        assert not any(kind == "finished" for kind, *_ in service.events)

    def test_voice_off_stops_and_on_starts_nothing(self, service, factory):
        speak(service)
        service.set_enabled(False)
        assert not service.is_speaking() and factory.engines[0].stopped == 1
        assert speak(service) is None and len(factory.engines) == 1

        service.set_enabled(True)
        assert not service.is_speaking()
        assert speak(service) is not None

    def test_shutdown_stops_and_rejects_everything_after(self, service, factory):
        speak(service)
        engine = factory.engines[0]
        service.shutdown()

        assert engine.stopped == 1 and not service.is_speaking()
        assert not service.available
        assert speak(service) is None and len(factory.engines) == 1
        engine.finish()
        assert not any(kind == "finished" for kind, *_ in service.events)


class TestRetiredFromTheStartedSignal:
    """A started listener may end the utterance; its engine must then stay silent."""

    @pytest.mark.parametrize(
        "action",
        [
            lambda s: s.stop(),
            lambda s: s.set_enabled(False),
            lambda s: s.shutdown(),
        ],
        ids=["stop", "voice-off", "shutdown"],
    )
    def test_ending_it_from_started_means_say_never_runs(self, service, factory, action):
        service.speech_started.connect(lambda _t: action(service))
        token = speak(service)

        engine = factory.engines[0]
        assert service.events == [("started", token)]
        assert engine.spoken == [] and engine.stopped == 1 and engine.disposed
        assert not service.is_speaking()

    def test_replacing_it_from_started_speaks_only_the_replacement(self, service, factory):
        replaced = []

        def replay_once(_token):
            if not replaced:
                replaced.append(service.speak("two", lesson_id=1, step_index=0))

        service.speech_started.connect(replay_once)
        first = speak(service, "one")

        old = factory.engines[0]
        assert old.spoken == [] and old.stopped == 1 and old.disposed
        new = factory.engines[1]
        assert new.spoken == ["two"] and service.current_token == replaced[0]
        assert [t for kind, t, *_ in service.events if kind == "started"] == [first, replaced[0]]


class TestFailure:
    def test_engine_error_reports_failure_once_and_retires(self, service, factory):
        token = speak(service)
        engine = factory.engines[0]
        engine.fail("device lost")

        assert service.events[-1] == ("failed", token, "device lost")
        assert service.events.count(service.events[-1]) == 1
        assert not service.is_speaking() and engine.disposed
        assert engine.stopped == 1, "playback is stopped, not left to deferred deletion"
        assert speak(service) is not None, "the service stays usable"

    def test_engine_in_error_at_creation_fails_with_the_returned_token(self, service, factory):
        factory.initial = ENGINE_ERROR
        token = speak(service, start=False)
        assert token is not None and service.events == []
        pump()
        assert service.events[-1][:2] == ("failed", token)
        assert not service.is_speaking()

    def test_factory_failure_is_reported_not_raised(self, service, factory):
        factory.raise_on_create = OSError("no device")
        token = speak(service)
        assert service.events[-1][:2] == ("failed", token) and "no device" in service.events[-1][2]
        assert not service.is_speaking()

    def test_stop_and_dispose_failures_never_raise(self, service, factory):
        def broken_stop():
            raise RuntimeError("backend gone")

        factory.on_stop = broken_stop
        token = speak(service)
        engine = factory.engines[0]
        engine.dispose = Mock(side_effect=RuntimeError("dispose gone"))

        engine.fail("device lost")  # error path: stop then dispose, both broken

        assert service.events[-1] == ("failed", token, "device lost")
        assert not service.is_speaking()
        service.stop()  # nothing current; still safe

        speak(service, "again")
        service.stop()  # stop path with a broken backend stop
        assert not service.is_speaking()

    def test_stopping_a_deleted_engine_never_raises_and_still_disposes(self, service, factory):
        # The Qt half of the engine is gone: reading its signals raises
        # RuntimeError. Retirement must not escape and must still reach
        # stop/dispose, even though stop itself fails on the dead object.
        speak(service)
        engine = factory.engines[0]
        sip.delete(engine)

        service.stop()

        assert not service.is_speaking()
        assert engine.disposed
        assert speak(service, "again") is not None, "the service stays usable"

    def test_error_query_failure_at_creation_still_fails_with_the_token(self, service, factory):
        factory.initial = ENGINE_ERROR
        create = factory.create

        def create_with_broken_error_string():
            engine = create()
            engine.error_string = Mock(side_effect=RuntimeError("error string gone"))
            return engine

        factory.create = create_with_broken_error_string
        token = speak(service, start=False)
        assert token is not None and service.events == []

        pump()

        engine = factory.engines[0]
        assert service.events[-1] == ("failed", token, "speech engine failed to start")
        assert not service.is_speaking()
        assert engine.stopped == 1 and engine.disposed

    def test_error_query_failure_on_a_later_error_still_fails_with_the_token(
        self, service, factory
    ):
        token = speak(service)
        engine = factory.engines[0]
        engine.error_string = Mock(side_effect=RuntimeError("error string gone"))

        engine.set_state(ENGINE_ERROR)

        assert service.events[-1] == ("failed", token, "speech engine error")
        assert service.events.count(service.events[-1]) == 1
        assert not service.is_speaking()
        assert engine.stopped == 1 and engine.disposed

    def test_unavailable_factory_means_off_and_silent(self, qt_app):
        service = NarrationService(factory=FakeFactory(available=False))
        assert not service.available and not service.enabled
        assert service.unavailable_reason == "no voice here"
        service.set_enabled(True)
        assert not service.enabled
        assert speak(service) is None


class TestQtStateMapping:
    def test_qt_state_names_map_to_the_three_engine_states(self):
        State = Enum("State", ["Ready", "Speaking", "Synthesizing", "Paused", "Error"])
        assert normalize_state(State.Ready) == ENGINE_READY
        assert normalize_state(State.Speaking) == ENGINE_BUSY
        assert normalize_state(State.Synthesizing) == ENGINE_BUSY
        assert normalize_state(State.Paused) == ENGINE_BUSY
        assert normalize_state(State.Error) == ENGINE_ERROR
        assert normalize_state("Whatever") == ENGINE_BUSY
