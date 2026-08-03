"""Unit tests for the floating companion (M6).

Runs under the offscreen platform plugin, so no display is required.
"""

import pytest
from PyQt6.QtWidgets import QApplication

from src.desktop.companion import FloatingCompanion
from src.input.input_manager import InputManager
from src.input.state_machine import TutorState


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def companion(qt_app):
    return FloatingCompanion()


class TestStateRendering:
    def test_starts_idle(self, companion):
        assert companion.lbl_status.text() == "READY"

    def test_idle_hides_navigation(self, companion):
        companion.apply_state(TutorState.IDLE)

        assert not companion.nav_widget.isVisibleTo(companion)

    @pytest.mark.parametrize(
        "state, expected",
        [(TutorState.CAPTURING, "CAPTURING"), (TutorState.ANALYZING, "THINKING")],
    )
    def test_busy_states_report_progress(self, companion, state, expected):
        companion.apply_state(state)

        assert companion.lbl_status.text() == expected

    def test_busy_state_animates(self, companion):
        companion.apply_state(TutorState.ANALYZING)

        assert companion._thinking_timer.isActive()

    def test_leaving_busy_stops_the_animation(self, companion):
        companion.apply_state(TutorState.ANALYZING)
        companion.apply_state(TutorState.TEACHING)

        assert not companion._thinking_timer.isActive()

    def test_teaching_shows_navigation(self, companion):
        companion.apply_state(TutorState.TEACHING)

        assert companion.nav_widget.isVisibleTo(companion)


class TestStepDisplay:
    STEP = {"title": "Initialising the counter", "explanation": "It tracks progress."}

    def test_renders_title_and_explanation(self, companion):
        companion.show_step(self.STEP, index=0, total=3)

        assert companion.lbl_title.text() == "Initialising the counter"
        assert companion.lbl_body.text() == "It tracks progress."

    def test_counter_is_one_based(self, companion):
        companion.show_step(self.STEP, index=0, total=3)

        assert companion.lbl_counter.text() == "1 / 3"

    def test_back_disabled_on_first_step(self, companion):
        companion.show_step(self.STEP, index=0, total=3)

        assert not companion.btn_prev.isEnabled()
        assert companion.btn_next.isEnabled()

    def test_next_disabled_on_last_step(self, companion):
        companion.show_step(self.STEP, index=2, total=3)

        assert companion.btn_prev.isEnabled()
        assert not companion.btn_next.isEnabled()

    def test_single_step_lesson_disables_both(self, companion):
        companion.show_step(self.STEP, index=0, total=1)

        assert not companion.btn_prev.isEnabled()
        assert not companion.btn_next.isEnabled()

    def test_tolerates_a_step_missing_fields(self, companion):
        companion.show_step({}, index=0, total=1)

        assert companion.lbl_title.text() == ""


class TestGeometryStability:
    """Guards the bug where the panel walked off the top of the screen.

    Observed live: y went 910 -> 318 -> 278 -> ... -> -2 across step changes,
    because each resize let Qt resolve the position afresh and the error
    accumulated.
    """

    SHORT = {"title": "A", "explanation": "Short."}
    LONG = {"title": "B", "explanation": "A much longer explanation. " * 12}

    def test_position_survives_alternating_content_heights(self, companion):
        companion.show_step(self.SHORT, 0, 4)
        start = (companion.x(), companion.y())

        for step in (self.LONG, self.SHORT, self.LONG, self.SHORT):
            companion.show_step(step, 1, 4)

        assert (companion.x(), companion.y()) == start

    def test_does_not_drift_over_many_steps(self, companion):
        # A taller step is legitimately lifted so it stays on screen, so the
        # invariant is that identical content always lands in the same place --
        # the error must not accumulate across cycles.
        companion.show_step(self.SHORT, 0, 20)
        start_y = companion.y()

        seen = []
        for i in range(20):
            companion.show_step(self.LONG, i, 20)
            companion.show_step(self.SHORT, i, 20)
            seen.append(companion.y())

        assert seen == [start_y] * 20

    def test_stays_within_the_screen(self, companion, qt_app):
        companion.show_step(self.LONG, 0, 4)
        area = qt_app.primaryScreen().availableGeometry()

        assert companion.y() >= area.top()
        assert companion.x() >= area.left()

    def test_width_never_exceeds_the_fixed_width(self, companion):
        # Qt warned "Unable to set geometry 475x... maximum size: 380" on every
        # step because wrapping labels reported a sizeHint wider than the window.
        companion.show_step(self.LONG, 0, 4)

        assert companion.width() == 380


class TestExplanationFitting:
    def test_short_text_is_untouched(self, companion):
        companion.show_step({"title": "t", "explanation": "Brief."}, 0, 1)

        assert companion.lbl_body.text() == "Brief."

    def test_long_text_is_truncated_with_an_ellipsis(self, companion):
        companion.show_step({"title": "t", "explanation": "word " * 300}, 0, 1)

        body = companion.lbl_body.text()
        assert body.endswith("…")
        assert len(body) <= 421

    def test_truncation_lands_on_a_word_boundary(self, companion):
        companion.show_step({"title": "t", "explanation": "alpha bravo " * 80}, 0, 1)

        # No half-word before the ellipsis.
        assert companion.lbl_body.text().rstrip("…").split()[-1] in {"alpha", "bravo"}


class TestSignals:
    def test_buttons_emit_navigation_requests(self, companion):
        seen = []
        companion.next_requested.connect(lambda: seen.append("next"))
        companion.prev_requested.connect(lambda: seen.append("prev"))
        companion.show_step({"title": "t", "explanation": "e"}, index=1, total=3)

        companion.btn_next.click()
        companion.btn_prev.click()

        assert seen == ["next", "prev"]


class TestStateSignalWiring:
    def test_input_manager_emits_on_transition(self, qt_app):
        manager = InputManager()
        seen = []
        manager.state_changed.connect(seen.append)

        manager.set_state(TutorState.ANALYZING)

        assert seen == [TutorState.ANALYZING]

    def test_no_signal_when_state_is_unchanged(self, qt_app):
        manager = InputManager()
        seen = []
        manager.state_changed.connect(seen.append)

        manager.set_state(TutorState.IDLE)

        assert seen == []

    def test_companion_follows_the_manager(self, qt_app, companion):
        manager = InputManager()
        manager.state_changed.connect(companion.apply_state)

        manager.set_state(TutorState.ANALYZING)

        assert companion.lbl_status.text() == "THINKING"
