"""The floating companion: the only window a learner is meant to look at.

Milestone 6. This replaces the developer control panel as the user-facing
surface. It renders from TutorState rather than being told what to display by
each call site, so it cannot drift out of sync with what the tutor is actually
doing.

Deliberately not built here: chat, settings, themes, lesson history. Those are
out of scope for Phase 3.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.input.state_machine import TutorState

# States that mean "the tutor is working"; the companion animates through them.
_BUSY_STATES = frozenset({TutorState.CAPTURING, TutorState.ANALYZING})

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

# Explanations are clipped mid-sentence if they overflow, which looks broken.
# Truncating explicitly is honest about it, and a companion is not the place
# for an essay -- the overlay is doing the pointing.
MAX_EXPLANATION_CHARS = 420

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
QPushButton {
    background-color: rgba(255, 255, 255, 22);
    color: #ffffff;
    border: none;
    border-radius: 7px;
    padding: 6px 14px;
    font-size: 12px;
}
QPushButton:hover:enabled { background-color: rgba(255, 255, 255, 45); }
QPushButton:disabled { color: rgba(255, 255, 255, 70); }
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


class FloatingCompanion(QWidget):
    """A small always-on-top panel showing what the tutor is doing.

    Signals are emitted rather than the controller being called directly, so
    the controller can route them through InputManager like every other action.
    """

    next_requested = pyqtSignal()
    prev_requested = pyqtSignal()
    dismiss_requested = pyqtSignal()

    def __init__(self, screen=None) -> None:
        super().__init__()
        self._screen_target = screen or QApplication.primaryScreen()
        self._drag_offset: QPoint | None = None
        self._desired_pos: tuple[int, int] | None = None
        self._thinking_dots = 0
        self._question = ""

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

        self.lbl_status = QLabel("READY")
        self.lbl_status.setObjectName("status")
        layout.addWidget(self.lbl_status)

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

        self.setLayout(layout)

    def _move_to_default_corner(self) -> None:
        if self._screen_target is None:
            return
        area = self._screen_target.availableGeometry()
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

        if self._desired_pos is None or self._screen_target is None:
            return

        x, y = self._desired_pos
        area = self._screen_target.availableGeometry()
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

        self.nav_widget.setVisible(teaching)

        if busy:
            self.lbl_status.setText("CAPTURING" if state == TutorState.CAPTURING else "THINKING")
            self.lbl_title.setText("Reading your screen…")
            self.lbl_body.setText("")
            self._start_thinking()
        elif teaching:
            self._stop_thinking()
            self.lbl_status.setText("TEACHING")
        else:
            self._stop_thinking()
            self.lbl_status.setText("READY")
            self.lbl_title.setText("Press Ctrl+Shift+A")
            self.lbl_body.setText("Ask about anything on your screen.")
            self.lbl_counter.setText("")
            self._question = ""
            self.lbl_question.setVisible(False)

        self._apply_geometry()

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
        self._apply_geometry()

    def show_message(self, status: str, title: str, body: str = "") -> None:
        """Shows a one-off message, e.g. an error the user should see."""
        self._stop_thinking()
        self.lbl_status.setText(status)
        self.lbl_title.setText(title)
        self.lbl_body.setText(body)
        self.nav_widget.setVisible(False)
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
            self.dismiss_requested.emit()
        else:
            super().keyPressEvent(event)
