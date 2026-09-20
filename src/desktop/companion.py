"""The floating companion: the only window a learner is meant to look at.

Milestone 6. This replaces the developer control panel as the user-facing
surface. It renders from TutorState rather than being told what to display by
each call site, so it cannot drift out of sync with what the tutor is actually
doing.

Narrated lessons, stage 1: the question is typed here rather than in a modal
dialog after the capture. The hotkey focuses the field; Enter asks. See
docs/narrated-lessons-plan.md.

Deliberately not built here: chat, settings, themes, lesson history.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QPoint, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QScreen
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.input.state_machine import TutorState

# States that mean "the tutor is working"; the companion animates through them.
_BUSY_STATES = frozenset({TutorState.CAPTURING, TutorState.ANALYZING, TutorState.ANSWERING})
_BUSY_STATUS = {
    TutorState.CAPTURING: ("CAPTURING", "Reading your screen…"),
    TutorState.ANALYZING: ("THINKING", "Reading your screen…"),
    TutorState.ANSWERING: ("ANSWERING", "Thinking about this step…"),
}

# States in which a lesson is on screen and navigation makes sense.
_LESSON_STATES = frozenset({TutorState.TEACHING, TutorState.FINISHED})

_WIDTH = 380
_PADDING = 18
# Wrapping labels report a sizeHint wider than the window unless their width is
# pinned, which made Qt request 475px against a 380px maximum and warn on every
# step change.
_CONTENT_WIDTH = _WIDTH - (_PADDING * 2)
_MARGIN = 24
_THINKING_INTERVAL_MS = 400

# Asked when the learner presses the hotkey or Enter with nothing typed. A
# blank question used to cancel the capture, which made the fastest path -- hit
# the hotkey, see what this is -- the one that did nothing.
DEFAULT_QUESTION = "Explain what I am looking at."

# Explanations are clipped mid-sentence if they overflow, which looks broken.
# Truncating explicitly is honest about it, and a companion is not the place
# for an essay -- the overlay is doing the pointing.
MAX_EXPLANATION_CHARS = 420
# A follow-up answer is asked for as 2-5 sentences, so it gets more room than
# a step body before the same honest truncation applies.
MAX_ANSWER_CHARS = 700

# The field's labels in the two modes it has: a new capture, or a question
# about the step on show.
ASK_PLACEHOLDER = "Ask about the screen, or press Enter"
FOLLOW_UP_PLACEHOLDER = "Ask about this step"
ASK_LABEL = "Ask"
FOLLOW_UP_LABEL = "Follow up"
ASK_SCREEN_LABEL = "Ask screen"

_STYLE = """
#companion {
    background-color: rgba(24, 24, 30, 235);
    border: 1px solid rgba(255, 255, 255, 40);
    border-radius: 14px;
}
#status { color: #8ab4f8; font-size: 11px; font-weight: bold; }
#question { color: #9aa0a6; font-size: 11px; font-style: italic; }
#title  { color: #ffffff; font-size: 15px; font-weight: bold; }
#body   { color: #d7d7db; font-size: 12px; }
#counter { color: #9aa0a6; font-size: 11px; }
QLineEdit {
    background-color: rgba(255, 255, 255, 18);
    color: #ffffff;
    border: 1px solid rgba(255, 255, 255, 40);
    border-radius: 7px;
    padding: 6px 10px;
    font-size: 12px;
}
QLineEdit:focus { border: 1px solid #8ab4f8; }
QPushButton {
    background-color: rgba(255, 255, 255, 22);
    color: #ffffff;
    border: none;
    border-radius: 7px;
    padding: 6px 14px;
    font-size: 12px;
}
#primaryAction {
    padding-left: 4px;
    padding-right: 4px;
}
QPushButton:hover:enabled { background-color: rgba(255, 255, 255, 45); }
QPushButton:disabled { color: rgba(255, 255, 255, 70); }
QCheckBox { color: #d7d7db; font-size: 12px; }
QCheckBox:disabled { color: rgba(255, 255, 255, 90); }
"""


def _fit(text: str, limit: int = MAX_EXPLANATION_CHARS) -> str:
    """Trims an explanation to what the panel can show without clipping.

    Cuts on a word boundary so the result reads as a sentence rather than
    stopping mid-word.
    """
    text = (text or "").strip()
    if len(text) <= limit:
        return text

    cut = text[:limit]
    space = cut.rfind(" ")
    if space > limit * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ,;:.") + "…"


def _try_available_geometry(screen: QScreen | None) -> QRect | None:
    """A screen's available geometry, or None if the screen is gone."""
    if screen is None:
        return None
    try:
        return screen.availableGeometry()
    except RuntimeError:
        return None


class FloatingCompanion(QWidget):
    """A small always-on-top panel showing what the tutor is doing.

    Signals are emitted rather than the controller being called directly, so
    the controller can route them through InputManager like every other action.
    """

    next_requested = pyqtSignal()
    prev_requested = pyqtSignal()
    # Qt saw a physical Escape while this window had keyboard focus. Nothing
    # is done about it here: the controller decides whether the global hook
    # is already the authority for that press, and what the press means.
    escape_pressed = pyqtSignal()
    # Carries the question to ask of a new capture. Never empty: a blank
    # field submits DEFAULT_QUESTION.
    question_submitted = pyqtSignal(str)
    # Carries a question about the step on show. Never blank: an empty field
    # in a lesson state submits nothing at all.
    follow_up_submitted = pyqtSignal(str)
    # The learner wants to pick an area for the next question.
    select_area_requested = pyqtSignal()
    # The learner wants the next question to use the full screen again.
    clear_area_requested = pyqtSignal()
    # The learner flipped the Voice switch; carries the new setting.
    voice_toggled = pyqtSignal(bool)
    # Read the current step aloud again, from the beginning.
    replay_requested = pyqtSignal()
    # Stop reading; the step stays on screen.
    stop_speech_requested = pyqtSignal()
    # Forget this session's questions, answers, capture and lesson; start over.
    new_session_requested = pyqtSignal()

    def __init__(self, screen=None) -> None:
        super().__init__()
        self._screen_target = screen or QApplication.primaryScreen()
        self._drag_offset: QPoint | None = None
        self._desired_pos: tuple[int, int] | None = None
        self._thinking_dots = 0
        self._question = ""
        # Human-readable size of the prepared area, or None for full screen.
        self._area_description: str | None = None
        self._voice_available = False
        self._voice_enabled = False
        self._voice_speaking = False
        self._state = TutorState.IDLE

        self.setObjectName("companion")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        # Never steal focus: the learner is working in another application and
        # a capture is about what *they* were looking at.
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet(_STYLE)
        self.setFixedWidth(_WIDTH)

        self._build_ui()

        self._thinking_timer = QTimer(self)
        self._thinking_timer.setInterval(_THINKING_INTERVAL_MS)
        self._thinking_timer.timeout.connect(self._tick_thinking)

        self.apply_state(TutorState.IDLE)
        self._move_to_default_corner()

    # ---------------------------------------------------------------- layout

    def _build_ui(self) -> None:
        layout = QVBoxLayout()
        layout.setContentsMargins(_PADDING, 14, _PADDING, 14)
        layout.setSpacing(8)

        # New session shares the status row so it stays reachable in every
        # state, busy ones included: it is how the learner abandons a request
        # they no longer want along with everything asked before it.
        header = QHBoxLayout()
        header.setSpacing(8)
        self.lbl_status = QLabel("READY")
        self.lbl_status.setObjectName("status")
        header.addWidget(self.lbl_status, stretch=1)
        self.btn_new_session = QPushButton("New session")
        self.btn_new_session.setToolTip(
            "Start over: forget this session's questions and answers, and clear the screen"
        )
        self.btn_new_session.clicked.connect(self.new_session_requested)
        header.addWidget(self.btn_new_session)
        layout.addLayout(header)

        # The question is the whole point of the interaction, and once the
        # input dialog closes nothing else on screen records what was asked.
        self.lbl_question = QLabel()
        self.lbl_question.setObjectName("question")
        self.lbl_question.setWordWrap(True)
        self.lbl_question.setFixedWidth(_CONTENT_WIDTH)
        self.lbl_question.setVisible(False)
        layout.addWidget(self.lbl_question)

        self.lbl_title = QLabel()
        self.lbl_title.setObjectName("title")
        self.lbl_title.setWordWrap(True)
        self.lbl_title.setFixedWidth(_CONTENT_WIDTH)
        layout.addWidget(self.lbl_title)

        self.lbl_body = QLabel()
        self.lbl_body.setObjectName("body")
        self.lbl_body.setWordWrap(True)
        self.lbl_body.setFixedWidth(_CONTENT_WIDTH)
        layout.addWidget(self.lbl_body)

        nav = QHBoxLayout()
        nav.setSpacing(8)
        self.btn_prev = QPushButton("‹ Back")
        self.btn_prev.clicked.connect(self.prev_requested)
        nav.addWidget(self.btn_prev)

        self.lbl_counter = QLabel()
        self.lbl_counter.setObjectName("counter")
        self.lbl_counter.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nav.addWidget(self.lbl_counter, stretch=1)

        self.btn_next = QPushButton("Next ›")
        self.btn_next.clicked.connect(self.next_requested)
        nav.addWidget(self.btn_next)

        self.nav_widget = QWidget()
        self.nav_widget.setLayout(nav)
        layout.addWidget(self.nav_widget)

        # Voice sits between the lesson and the question so the switch is at
        # hand before asking; Replay and Stop appear only with a step on show.
        voice = QHBoxLayout()
        voice.setSpacing(8)
        self.chk_voice = QCheckBox("Voice")
        # clicked, not toggled: programmatic setChecked() from the controller
        # must not report back as a learner's request and stop the reading.
        self.chk_voice.clicked.connect(self.voice_toggled)
        voice.addWidget(self.chk_voice)
        self.lbl_voice_notice = QLabel()
        self.lbl_voice_notice.setObjectName("counter")
        voice.addWidget(self.lbl_voice_notice, stretch=1)
        self.btn_replay = QPushButton("Replay")
        self.btn_replay.setToolTip("Read this step again")
        self.btn_replay.clicked.connect(self.replay_requested)
        voice.addWidget(self.btn_replay)
        self.btn_stop_speech = QPushButton("Stop")
        self.btn_stop_speech.setToolTip("Stop reading; the step stays")
        self.btn_stop_speech.clicked.connect(self.stop_speech_requested)
        voice.addWidget(self.btn_stop_speech)

        self.voice_widget = QWidget()
        self.voice_widget.setLayout(voice)
        layout.addWidget(self.voice_widget)

        ask = QHBoxLayout()
        ask.setSpacing(8)
        self.question_input = QLineEdit()
        self.question_input.setPlaceholderText(ASK_PLACEHOLDER)
        self.question_input.returnPressed.connect(self.submit_question)
        # QLineEdit ignores Escape, which would propagate it to this widget's
        # keyPressEvent and report the same press twice. The filter consumes
        # it so one press is reported once.
        self.question_input.installEventFilter(self)
        ask.addWidget(self.question_input, stretch=1)

        self.btn_ask = QPushButton(ASK_LABEL)
        self.btn_ask.setObjectName("primaryAction")
        # Horizontally fixed: the Windows style otherwise lets the button
        # absorb spare width and squeezes the field the learner types in.
        # The button still resizes to its label when it is relabelled.
        self.btn_ask.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.btn_ask.clicked.connect(self.submit_question)
        ask.addWidget(self.btn_ask)

        # The explicit recapture while a lesson is on show. The field means
        # "about this step" then, so a screen that changed needs its own
        # control rather than a hotkey the learner may not remember.
        self.ask_widget = QWidget()
        self.ask_widget.setLayout(ask)
        layout.addWidget(self.ask_widget)

        # The screen actions sit on their own row: with Follow up beside the
        # field, a third and fourth button in the same 380px row left the
        # field too narrow to type in.
        screen = QHBoxLayout()
        screen.setSpacing(8)
        screen.addStretch(1)
        self.btn_ask_screen = QPushButton(ASK_SCREEN_LABEL)
        self.btn_ask_screen.setToolTip("Capture the screen again and start a new lesson")
        self.btn_ask_screen.clicked.connect(self.submit_ask_screen)
        self.btn_ask_screen.setVisible(False)
        screen.addWidget(self.btn_ask_screen)

        self.btn_select_area = QPushButton("Select area")
        self.btn_select_area.setToolTip("Pick the part of the screen to ask about (Ctrl+Shift+S)")
        self.btn_select_area.clicked.connect(self.select_area_requested)
        screen.addWidget(self.btn_select_area)

        self.screen_widget = QWidget()
        self.screen_widget.setLayout(screen)
        layout.addWidget(self.screen_widget)

        # A short inline notice under the field, e.g. a follow-up that failed.
        # The lesson stays; there is no dialog to dismiss.
        self.lbl_notice = QLabel()
        self.lbl_notice.setObjectName("counter")
        self.lbl_notice.setWordWrap(True)
        self.lbl_notice.setFixedWidth(_CONTENT_WIDTH)
        self.lbl_notice.setVisible(False)
        layout.addWidget(self.lbl_notice)

        # Shown only while an area is prepared, so the learner can see that
        # Ask will crop and can go back to the full screen in one click.
        area = QHBoxLayout()
        area.setSpacing(8)
        self.lbl_area = QLabel()
        self.lbl_area.setObjectName("counter")
        area.addWidget(self.lbl_area, stretch=1)
        self.btn_clear_area = QPushButton("Use full screen")
        self.btn_clear_area.clicked.connect(self.clear_area_requested)
        area.addWidget(self.btn_clear_area)

        self.area_widget = QWidget()
        self.area_widget.setLayout(area)
        self.area_widget.setVisible(False)
        layout.addWidget(self.area_widget)

        self.setLayout(layout)

    def _available_area(self) -> QRect | None:
        """The screen area the panel may occupy, or None when no screen is left.

        The retained QScreen is a Python wrapper over a C++ object that Qt
        deletes when a monitor is unplugged; every accessor then raises
        RuntimeError. That must not escape a Qt slot, and the recovery message
        the controller shows next has to land somewhere visible, so a dead
        target is dropped in favour of whichever screen still exists. This only
        moves the companion: it does not retarget the overlay or say anything
        about whether a selected crop is still valid.
        """
        area = _try_available_geometry(self._screen_target)
        if area is not None:
            return area

        self._screen_target = None
        for candidate in (self.screen(), QApplication.primaryScreen()):
            area = _try_available_geometry(candidate)
            if area is not None:
                self._screen_target = candidate
                return area
        return None

    def _move_to_default_corner(self) -> None:
        area = self._available_area()
        if area is None:
            return
        self.adjustSize()
        self._desired_pos = (
            area.right() - self.width() - _MARGIN,
            area.bottom() - self.height() - _MARGIN,
        )
        self._apply_geometry()

    def _apply_geometry(self) -> None:
        """Resizes to fit the content, then restores the intended position.

        Height changes between steps, and letting Qt resolve the position each
        time made the window creep: it drifted upward by the height delta on
        every step change and eventually left the top of the screen. Re-applying
        a stored intent instead of reading back the current geometry means the
        error cannot accumulate.
        """
        self.adjustSize()

        if self._desired_pos is None:
            return

        area = self._available_area()
        if area is None:
            return

        x, y = self._desired_pos
        # Clamp so a tall step cannot push the panel off-screen.
        x = max(area.left(), min(x, area.right() - self.width()))
        y = max(area.top(), min(y, area.bottom() - self.height()))
        self.move(x, y)

    # ----------------------------------------------------------- state entry

    def apply_state(self, state: TutorState) -> None:
        """Renders the companion for a tutor state.

        This is the only entry point for state-driven changes; show_step()
        supplies the lesson content once TEACHING has been entered.
        """
        busy = state in _BUSY_STATES
        teaching = state in _LESSON_STATES
        selecting = state is TutorState.SELECTING
        self._state = state

        self.nav_widget.setVisible(teaching)
        # While the tutor is working a new question would be dropped by the
        # state guard anyway, so do not offer the field.
        self.ask_widget.setVisible(not busy and not selecting)
        self.screen_widget.setVisible(not busy and not selecting)
        self.area_widget.setVisible(
            not busy and not selecting and self._area_description is not None
        )
        self._refresh_ask_row(teaching)
        if busy or state is TutorState.IDLE:
            self.set_notice("")

        if busy:
            status, title = _BUSY_STATUS[state]
            self.lbl_status.setText(status)
            self.lbl_title.setText(title)
            self.lbl_body.setText("")
            self._start_thinking()
        elif selecting:
            # The question and any lesson text are left alone: selecting only
            # prepares an area, and the panel comes back as it was.
            self._stop_thinking()
            self.lbl_status.setText("SELECTING")
            self.lbl_title.setText("Drag over the area to ask about")
            self.lbl_body.setText("Release to keep it. Esc cancels.")
        elif teaching:
            self._stop_thinking()
            self.lbl_status.setText("TEACHING")
        else:
            self._stop_thinking()
            self.lbl_status.setText("READY")
            self.lbl_title.setText("Type a question, or press Ctrl+Shift+A")
            self.lbl_body.setText("Enter asks about what is on your screen.")
            self.lbl_counter.setText("")
            self._question = ""
            self.lbl_question.setVisible(False)

        self._refresh_voice_row()
        self._apply_geometry()

    def _refresh_ask_row(self, teaching: bool) -> None:
        """Relabels the field for its mode: a new capture, or the step on show."""
        self.question_input.setPlaceholderText(
            FOLLOW_UP_PLACEHOLDER if teaching else ASK_PLACEHOLDER
        )
        self.btn_ask.setText(FOLLOW_UP_LABEL if teaching else ASK_LABEL)
        # A fixed policy holds whatever size the button had when laid out;
        # after a relabel that must be the new label's natural size.
        self.btn_ask.adjustSize()
        self.btn_ask.updateGeometry()
        self.btn_ask_screen.setVisible(teaching)

    def follow_up_mode(self) -> bool:
        """True when the field asks about the displayed step, not the screen."""
        return self._state in _LESSON_STATES

    def set_notice(self, text: str) -> None:
        """Shows a short inline note under the field, or hides it when blank."""
        text = (text or "").strip()
        self.lbl_notice.setText(text)
        self.lbl_notice.setVisible(bool(text))
        self._apply_geometry()

    def show_follow_up(self, question: str, answer: str) -> None:
        """Shows a follow-up and its answer in place of the step's own text.

        The step counter, navigation and highlight are untouched: the answer
        is about the step that stays on show.
        """
        self._stop_thinking()
        self.lbl_status.setText("TEACHING")
        self.lbl_title.setText(f"“{_fit(question, 120)}”")
        self.lbl_body.setText(_fit(answer, MAX_ANSWER_CHARS))
        self._apply_geometry()

    def set_voice_controls(
        self,
        available: bool,
        enabled: bool,
        speaking: bool,
        notice: str = "",
        unavailable_reason: str = "",
    ) -> None:
        """Renders the voice row from the narration service's state.

        Args:
            available: A local voice exists on this machine.
            enabled: The learner has the voice switched on.
            speaking: A step is being read right now.
            notice: A short note to show beside the switch, e.g. after a
                failure; empty clears it.
            unavailable_reason: Why there is no voice, shown as a tooltip.
        """
        self._voice_available = available
        self._voice_enabled = enabled and available
        self._voice_speaking = speaking and available
        self.chk_voice.setEnabled(available)
        self.chk_voice.setChecked(self._voice_enabled)
        self.chk_voice.setText("Voice" if available else "Voice unavailable")
        self.chk_voice.setToolTip(
            "Read each step aloud" if available else unavailable_reason or "No local voice"
        )
        self.lbl_voice_notice.setText(notice)
        self._refresh_voice_row()
        self._apply_geometry()

    def _refresh_voice_row(self) -> None:
        busy = self._state in _BUSY_STATES or self._state is TutorState.SELECTING
        self.voice_widget.setVisible(not busy)
        # Only a step on show can be replayed; a message that replaced the
        # step hides the navigation and with it these two.
        playable = self._voice_available and self.nav_widget.isVisibleTo(self)
        self.btn_replay.setVisible(playable)
        self.btn_stop_speech.setVisible(playable)
        self.btn_replay.setEnabled(self._voice_enabled)
        self.btn_stop_speech.setEnabled(self._voice_speaking)
        self.lbl_voice_notice.setVisible(bool(self.lbl_voice_notice.text()))

    def set_selected_area(self, description: str | None) -> None:
        """Shows that the next Ask will crop to an area, or hides the indicator.

        Args:
            description: A short size string such as ``"640 × 400"``, or None
                when the next capture is the full screen.
        """
        self._area_description = description
        self.lbl_area.setText(f"Area selected · {description}" if description else "")
        self.area_widget.setVisible(description is not None and self.ask_widget.isVisibleTo(self))
        self._apply_geometry()

    def has_selected_area(self) -> bool:
        return self._area_description is not None

    def draft_text(self) -> str:
        """What has been typed and not yet asked, verbatim."""
        return self.question_input.text()

    def restore_draft(self, text: str) -> None:
        """Puts a draft back after the field was temporarily withdrawn."""
        self.question_input.setText(text)

    def set_question(self, question: str) -> None:
        """Records what the learner asked, for the life of the lesson."""
        question = (question or "").strip()
        self._question = question
        self.lbl_question.setText(f"“{_fit(question, 120)}”" if question else "")
        self.lbl_question.setVisible(bool(question))
        self._apply_geometry()

    def show_step(self, step: dict, index: int, total: int) -> None:
        """Displays one lesson step.

        Args:
            step: A parsed lesson step.
            index: Zero-based position of the step.
            total: Number of steps in the lesson.
        """
        self._stop_thinking()
        self.lbl_status.setText("TEACHING")
        self.lbl_title.setText(step.get("title", ""))
        self.lbl_body.setText(_fit(step.get("explanation", "")))
        self.lbl_counter.setText(f"{index + 1} / {total}")
        self.nav_widget.setVisible(True)
        self.btn_prev.setEnabled(index > 0)
        self.btn_next.setEnabled(index < total - 1)
        self._refresh_voice_row()
        self._apply_geometry()

    # ------------------------------------------------------------- asking

    def focus_question(self) -> None:
        """Puts the cursor in the question field, e.g. from the hotkey."""
        if not self.isVisible():
            self.show()
        self.activateWindow()
        self.raise_()
        self.question_input.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def question_has_focus(self) -> bool:
        return self.question_input.hasFocus()

    def has_draft_text(self) -> bool:
        """True when something has been typed and not yet asked."""
        return bool(self.question_input.text().strip())

    def abandon_draft(self) -> None:
        """Drops the typed question and gives the field up.

        Leaves the lesson behind it alone; Escape while drafting is about the
        draft, not the lesson.
        """
        self.question_input.clear()
        self.question_input.clearFocus()

    def submit_question(self) -> None:
        """Submits the field for its current mode.

        With no lesson on show: the typed question, or the default when
        nothing was typed, for a new capture. With a step on show: a
        follow-up about that step; a blank field submits nothing.
        """
        if not self.ask_widget.isVisibleTo(self):
            return
        if self.follow_up_mode():
            question = self.question_input.text().strip()
            if not question:
                return
            self._take_field()
            self.follow_up_submitted.emit(question)
            return
        self.submit_ask_screen()

    def submit_ask_screen(self) -> None:
        """Asks of a fresh capture: the typed question, or the default."""
        if not self.ask_widget.isVisibleTo(self):
            return
        question = self.question_input.text().strip() or DEFAULT_QUESTION
        self._take_field()
        self.question_submitted.emit(question)

    def _take_field(self) -> None:
        self.question_input.clear()
        self.question_input.clearFocus()

    def eventFilter(self, watched, event) -> bool:
        if (
            watched is self.question_input
            and event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Escape
        ):
            self._report_escape(event)
            return True
        return super().eventFilter(watched, event)

    def _report_escape(self, event) -> None:
        # A held key auto-repeats. Reporting each repeat would let one press
        # abandon the draft and then dismiss the lesson behind it.
        if event.isAutoRepeat():
            return
        self.escape_pressed.emit()

    def show_message(self, status: str, title: str, body: str = "") -> None:
        """Shows a one-off message, e.g. an error the user should see."""
        self._stop_thinking()
        self.lbl_status.setText(status)
        self.lbl_title.setText(title)
        self.lbl_body.setText(body)
        self.nav_widget.setVisible(False)
        self._refresh_voice_row()
        self._apply_geometry()

    # ------------------------------------------------------------- thinking

    def _start_thinking(self) -> None:
        self._thinking_dots = 0
        if not self._thinking_timer.isActive():
            self._thinking_timer.start()

    def _stop_thinking(self) -> None:
        if self._thinking_timer.isActive():
            self._thinking_timer.stop()

    def _tick_thinking(self) -> None:
        self._thinking_dots = (self._thinking_dots + 1) % 4
        self.lbl_body.setText("•" * self._thinking_dots)

    # ------------------------------------------------------------- dragging

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_offset is not None:
            # Remember where the user put it, so a later step change restores
            # that position rather than snapping back to the corner.
            self._desired_pos = (self.x(), self.y())
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._report_escape(event)
        else:
            super().keyPressEvent(event)
