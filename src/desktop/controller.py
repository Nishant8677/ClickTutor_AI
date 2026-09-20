import logging
import time
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.attention.coordinates import CaptureGeometry
from src.attention.overlay import TransparentOverlay
from src.attention.screens import physical_region
from src.attention.shapes import (
    CircleShape,
    DebugBoxShape,
    RectangleShape,
    UnderlineShape,
)
from src.desktop.companion import DEFAULT_QUESTION, FloatingCompanion
from src.desktop.narration import NarrationService, NarrationToken
from src.desktop.selection import (
    RegionSelector,
    ScreenSignature,
    SelectedRegion,
    SelectionStaleError,
    screen_signature,
)
from src.desktop.session import Exchange, StudySession
from src.input import InputAction, InputManager, TutorState
from src.locator import STEP_LOCATION_KEY, OcrLocator
from src.ocr_locator import build_words, extract_ocr_data
from src.vision_locator import locate_phrase

logger = logging.getLogger(__name__)

# The second opinion asked for when OCR cannot place an anchor as a phrase.
# Named here rather than passed down from main so the desktop path has one
# obvious place to disable it; set to None to run OCR-only.
VISION_LOCATOR = locate_phrase

# Attention types the renderer can actually draw. The prompt and the validator
# both also permit "arrow", but relationship arrows are deferred to Phase 4, so
# such a step is drawn as a rectangle and logged rather than failing silently.
RENDERABLE_ATTENTIONS = frozenset({"circle", "underline", "rectangle", "none"})

# A second hotkey press within this window asks with whatever has been typed,
# even if Windows refused to focus the companion's field on the first press.
# Focus from a global hook is not guaranteed -- the foreground-lock rule can
# leave the field unfocused and the taskbar flashing -- so the fast path must
# not depend on it. Long enough to type a short question; short enough that a
# press ten minutes later reads as a fresh start.
HOTKEY_ASK_WINDOW_SECONDS = 20.0

# Time for the window manager to finish removing the companion before a grab.
# Hiding and capturing in the same tick races, and the panel shows up in the
# screenshot.
COMPOSITOR_SETTLE_SECONDS = 0.08

# OCR confidence below which a debug box also gets a text label. Tesseract
# scores confident reads in the 90s, so this surfaces the doubtful ones --
# which is where a missed anchor comes from -- without labelling everything.
DEBUG_LABEL_CONFIDENCE = 70.0

# Shown when OCR reads nothing at all. ClickTutor locates highlights by
# searching OCR output, so with no words there is nothing to point at.
UNREADABLE_SCREEN_MESSAGE = (
    "I couldn't read any text on that screen.\n\n"
    "ClickTutor finds things to point at by reading the text on your screen, "
    "so it needs readable text — code, documentation, or a web page works best. "
    "Try zooming in, or capturing an area with clearer text."
)

# Given to the engine in place of the "original explanation" when the image is
# a crop the learner chose. Definitions and surrounding code may legitimately
# be outside the picture, and the model must say so rather than invent them.
# The learner's displayed question is not changed.
CROPPED_CAPTURE_NOTE = (
    "The screenshot is a region of the student's screen that the student "
    "selected on purpose; the rest of the screen is not visible to you. If a "
    "definition, import, earlier line or other context needed to answer is not "
    "inside this region, say that it is outside the selected area and ask the "
    "student to include it, instead of guessing what it contains."
)

# Shown when a stored selection no longer matches the screen it was made on.
STALE_SELECTION_MESSAGE = (
    "Your screen changed since you selected that area. Select it again, or use the full screen."
)

SELECTION_UNAVAILABLE_MESSAGE = (
    "Area selection isn't available with this capture method; the full screen is used."
)

# The states a selection can start from and return to.
_SELECTION_READY_STATES = frozenset({TutorState.IDLE, TutorState.TEACHING, TutorState.FINISHED})


@dataclass(frozen=True)
class LessonRequest:
    """One question about one captured screen.

    The serial is assigned before the capture begins and, together with the
    session id below, decides whether a result may touch the UI: cancelling,
    asking again or starting a demo retires the serial, New session retires
    the session, and anything a worker later reports for either is dropped
    unseen. The image travels with
    the request so an accepted lesson is always drawn on the screen it was
    generated from, never on a newer capture; the geometry travels with it
    for the same reason, so a crop is always placed where it was grabbed.
    """

    serial: int
    question: str
    image: Any
    # None for a whole-screen capture, which the overlay fits across itself.
    geometry: CaptureGeometry | None = None
    # The screen the crop was measured against, snapshotted at capture time.
    # OCR and the model take seconds, and a scale or resolution change in
    # that window would leave the geometry describing a screen that no
    # longer exists; the result is checked against this before it is drawn.
    # None for a whole-screen capture.
    signature: ScreenSignature | None = None
    # The study session this question was asked in. A result reported after
    # the learner started a new session is dropped even if nothing else has
    # retired its serial yet.
    session_id: int = 0
    # The accepted exchanges before this question, frozen at the moment the
    # request was made. Later answers and a session reset cannot reach it.
    history: tuple[Exchange, ...] = ()

    def history_messages(self) -> list[dict[str, str]]:
        """The snapshot in the role/content list LessonEngine reads. Fresh dicts each call."""
        return [message for exchange in self.history for message in exchange.messages()]


@dataclass(frozen=True)
class _SelectionSession:
    """Everything the controller must restore when the selector goes away."""

    selector: RegionSelector
    signature: ScreenSignature
    screen_region: dict[str, int]
    prior_state: TutorState
    draft: str
    companion_was_visible: bool
    overlay_was_visible: bool
    dev_panel_was_visible: bool


class LessonWorker(QThread):
    # Named lesson_ready rather than finished: QThread already defines a
    # finished() signal, and redeclaring it here shadowed Qt's own.
    lesson_ready = pyqtSignal(dict, list, str)
    error = pyqtSignal(str)

    def __init__(self, request: LessonRequest):
        super().__init__()
        self.request = request
        self.image = request.image
        self.question = request.question

    def run(self):
        try:
            from src.lesson_engine import LessonEngine

            # Cancellation is cooperative. Cancel reaches this thread as an
            # interruption request; it is honoured at the two points where
            # the next stage has not started yet. A model call already in
            # flight runs to completion under the engine's own caps and its
            # result is then discarded by the controller, so cancelling the
            # UI does not abort a provider request, it only stops the next
            # one from starting.
            if self.isInterruptionRequested():
                logger.info("Lesson request %s cancelled before OCR.", self.request.serial)
                return

            # OCR runs on this thread, not the GUI thread. Tesseract against a
            # 3x-upscaled full-screen image takes long enough to visibly freeze
            # the UI, and it has to finish before Gemini can be called anyway.
            ocr_data = extract_ocr_data(self.image)

            if self.isInterruptionRequested():
                logger.info(
                    "Lesson request %s cancelled during OCR; not asking the model.",
                    self.request.serial,
                )
                return

            # A screen Tesseract cannot read used to be a dead end: no OCR
            # words meant no anchor could ever resolve, so generating a lesson
            # only bought an explanation pointing at nothing.
            #
            # That is no longer true. The vision locator reads handwriting,
            # rotated labels and photographed pages that Tesseract cannot --
            # 23 of 24 targets on the hostile corpus, against OCR's 7. So bail
            # only when there is no fallback to bail to.
            if not build_words(ocr_data) and VISION_LOCATOR is None:
                logger.warning("OCR produced no usable words; skipping lesson generation.")
                self.error.emit(UNREADABLE_SCREEN_MESSAGE)
                return

            engine = LessonEngine(self.image, ocr_data, vision_locator=VISION_LOCATOR)
            # The engine's context slot is the existing seam for telling the
            # model what it is looking at; a crop needs it, a full screen not.
            context = CROPPED_CAPTURE_NOTE if self.request.geometry is not None else ""
            answer, _, steps = engine.generate_lesson(
                self.question, self.request.history_messages(), context
            )
            self.lesson_ready.emit(ocr_data, steps, answer)
        except Exception as e:
            logger.exception("Lesson generation failed")
            self.error.emit(str(e))


class DesktopUI(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.setWindowTitle("ClickTutor")
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint)
        self.resize(350, 350)

        layout = QVBoxLayout()

        # Demo Mode Section
        demo_layout = QHBoxLayout()
        self.demo_dropdown = QComboBox()
        demo_layout.addWidget(self.demo_dropdown)

        self.btn_demo = QPushButton("▶ Watch Demo")
        self.btn_demo.clicked.connect(self.on_watch_demo)
        demo_layout.addWidget(self.btn_demo)

        self.btn_record = QPushButton("⏺ Record MP4")
        self.btn_record.clicked.connect(self.on_record_demo)
        demo_layout.addWidget(self.btn_record)

        self.chk_fake_demo = QCheckBox("Presentation Mode (Video Record)")
        self.chk_fake_demo.setToolTip(
            "If checked, 'Capture & Ask' will play the selected offline demo instead of calling AI."
        )
        demo_layout.addWidget(self.chk_fake_demo)

        layout.addLayout(demo_layout)

        self.lbl_status = QLabel("Ready. Ask a question about the screen:")
        self.lbl_status.setWordWrap(True)
        layout.addWidget(self.lbl_status)

        self.text_question = QTextEdit()
        self.text_question.setPlaceholderText("e.g. What does count do?")
        self.text_question.setMaximumHeight(80)
        layout.addWidget(self.text_question)

        self.btn_capture = QPushButton("Capture & Ask")
        self.btn_capture.clicked.connect(
            lambda: self.controller.ask(self.text_question.toPlainText())
        )
        layout.addWidget(self.btn_capture)

        nav_layout = QHBoxLayout()
        # Navigation routes through InputManager like every other action, so
        # the state guard applies uniformly instead of only to the hotkeys.
        self.btn_prev = QPushButton("< Previous Step")
        self.btn_prev.clicked.connect(
            lambda: self.controller.input_manager.handle_action(InputAction.PREV_STEP)
        )
        nav_layout.addWidget(self.btn_prev)

        self.btn_next = QPushButton("Next Step >")
        self.btn_next.clicked.connect(
            lambda: self.controller.input_manager.handle_action(InputAction.NEXT_STEP)
        )
        nav_layout.addWidget(self.btn_next)

        layout.addLayout(nav_layout)

        self.btn_debug = QPushButton("Toggle OCR Debug Mode (F8)")
        self.btn_debug.clicked.connect(
            lambda: self.controller.input_manager.handle_action(InputAction.TOGGLE_DEBUG)
        )
        layout.addWidget(self.btn_debug)

        self.btn_quit = QPushButton("Exit ClickTutor")
        self.btn_quit.clicked.connect(QApplication.quit)
        layout.addWidget(self.btn_quit)

        self.setLayout(layout)

    def populate_demos(self, demos):
        self.demo_dropdown.clear()
        for demo_id, meta in demos.items():
            title = meta.get("title", demo_id)
            self.demo_dropdown.addItem(title, userData=demo_id)

    def on_watch_demo(self):
        demo_id = self.demo_dropdown.currentData()
        if demo_id:
            self.controller.start_demo(demo_id)

    def on_record_demo(self):
        demo_id = self.demo_dropdown.currentData()
        if demo_id:
            self.lbl_status.setText(f"Recording {demo_id}...")
            self.controller.start_recording(demo_id)

    def keyPressEvent(self, event):
        # Escape is reported to the controller, which owns the decision of
        # whether the global hook already handles this press. It must not also
        # take the "any key interrupts the demo" path below, or one press
        # would cancel twice.
        if event.key() == Qt.Key.Key_Escape:
            if not event.isAutoRepeat():
                self.controller.on_local_escape()
            return

        # Interrupt demo on any key press in UI
        if self.controller.demo_manager.is_running:
            self.controller.input_manager.handle_action(InputAction.CANCEL_LESSON)

        if event.key() == Qt.Key.Key_F8:
            self.controller.input_manager.handle_action(InputAction.TOGGLE_DEBUG)
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event):
        # Interrupt demo on any mouse click in UI
        if self.controller.demo_manager.is_running:
            self.controller.input_manager.handle_action(InputAction.CANCEL_LESSON)
        super().mousePressEvent(event)


class DesktopController:
    def __init__(self, default_image=None, show_dev_panel=False, narration=None):
        from src.capture import ScreenCapture
        from src.desktop.demo_manager import DemoManager
        from src.desktop.recorder import Mp4Recorder
        from src.input.hotkeys import HotkeyManager

        self.image_path = default_image
        self.current_image = None
        self.ocr_data = None
        # M7: the developer panel is opt-in. By default the learner sees only
        # the overlay and the companion, which is what a demo should show.
        self.show_dev_panel = show_dev_panel

        # Injected rather than called directly, so an AI-based locator can
        # replace this without the controller changing. See src/locator/.
        self.locator = OcrLocator()

        self.input_manager = InputManager()
        self.input_manager.add_listener(self._on_input_action)

        self.hotkeys = HotkeyManager()
        # Queued explicitly: hotkeys arrive on the keyboard library's listener
        # thread, and everything downstream touches Qt widgets. InputManager is
        # a QObject built here on the GUI thread, so a queued connection posts
        # the action to the main event loop instead of running it inline.
        self.hotkeys.action_triggered.connect(
            self.input_manager.handle_action,
            Qt.ConnectionType.QueuedConnection,
        )

        # Ensure hotkeys are unregistered when the application closes
        if QApplication.instance():
            QApplication.instance().aboutToQuit.connect(self.hotkeys.stop)

        self.overlay = TransparentOverlay()
        self.ui = DesktopUI(self)

        # The companion renders from state rather than being told what to show
        # at each call site, so it cannot drift out of sync with the tutor.
        self.companion = FloatingCompanion(screen=self.overlay.screen_target)
        self.input_manager.state_changed.connect(self.companion.apply_state)
        self.companion.next_requested.connect(
            lambda: self.input_manager.handle_action(InputAction.NEXT_STEP)
        )
        self.companion.prev_requested.connect(
            lambda: self.input_manager.handle_action(InputAction.PREV_STEP)
        )
        self.companion.escape_pressed.connect(self.on_local_escape)
        self.companion.question_submitted.connect(self.ask)
        self.companion.select_area_requested.connect(
            lambda: self.input_manager.handle_action(InputAction.SELECT_REGION)
        )
        self.companion.clear_area_requested.connect(
            lambda: self.input_manager.handle_action(InputAction.CLEAR_REGION)
        )
        self.companion.new_session_requested.connect(self.start_new_session)

        # Spoken narration runs beside the displayed lesson and never drives
        # it: finishing a step waits for Next. Injectable so tests use a fake
        # engine and so an unavailable voice leaves a text-only app.
        self.narration: NarrationService = (
            narration if narration is not None else NarrationService()
        )
        self.narration.speech_started.connect(self._on_speech_started)
        self.narration.speech_finished.connect(self._on_speech_finished)
        self.narration.speech_failed.connect(self._on_speech_failed)
        self.companion.voice_toggled.connect(self.set_voice_enabled)
        self.companion.replay_requested.connect(self.replay_narration)
        self.companion.stop_speech_requested.connect(self.stop_narration)
        if QApplication.instance():
            QApplication.instance().aboutToQuit.connect(self.narration.shutdown)
        # Identity of the lesson on screen, independent of request ownership:
        # a selection abandons the request but keeps the lesson, and Replay
        # must still know which lesson it is reading.
        self._displayed_lesson = 0
        # The one utterance whose callbacks may touch the UI: the full token
        # (lesson, step, generation) returned by the last accepted speak().
        # Anything else is a leftover from an earlier reading and is ignored,
        # so a late failure from a replay cannot mark a later step failed.
        self._expected_speech: NarrationToken | None = None

        self.capture_engine = ScreenCapture()
        self.demo_manager = DemoManager(self.capture_engine)

        self.ui.populate_demos(self.demo_manager.get_available_demos())

        self.demo_manager.demo_started.connect(self._on_demo_started)
        self.demo_manager.demo_stopped.connect(self._on_demo_stopped)

        # Recorder for Demo Videos
        self.recorder = Mp4Recorder(overlay=self.overlay, fps=15)
        self.recorder.recording_finished.connect(self._on_recording_finished)
        self.is_recording_mode = False
        self.demo_manager.step_changed.connect(self._on_demo_step_changed)

        self.lesson_steps = []
        self.current_step_index = 0
        self.is_debug_mode = False
        # At most one LessonWorker runs at a time, and it is referenced here
        # until its QThread reports finished. A cancelled worker is not
        # dropped early -- destroying a running QThread aborts the process --
        # and it is not waited on either, since that would freeze the GUI for
        # the length of a model call. It is asked to stop and left to finish.
        self.worker = None
        # A request captured while the cancelled worker is still finishing.
        # Only the latest is kept, so repeated cancel/ask cannot pile up model
        # requests; it starts when the running worker's finished signal lands.
        self._pending_lesson: LessonRequest | None = None
        # Monotonic id of the request that owns the UI, or None when nothing
        # does. Every result is checked against it before touching anything.
        self._request_serial = 0
        self._active_request: int | None = None
        # The study session: accepted questions and answers since launch or
        # the last New session, held only in memory. Its id is the second
        # half of request ownership: a result must match the serial *and*
        # the session it was asked in before it may touch anything.
        self._session_serial = 1
        self.session = StudySession(self._session_serial)
        # Serial of the request whose error dialog is open, or None. The dialog
        # runs while the state is already IDLE, so without this an Escape
        # pressed over it would fall through as "nothing to cancel" and the
        # dialog's Yes could still start the fallback demo afterwards.
        self._modal_request: int | None = None
        # Set by ask() just before the ASK action is routed, since actions are
        # plain enum values and cannot carry the text themselves.
        self._pending_question = ""
        # When the hotkey last armed the question field; None when it has not.
        self._question_armed_at = None
        # The area prepared for the *next* capture, kept until cleared or
        # replaced. Deliberately separate from _displayed_geometry: clearing
        # the selection must not move the highlights of a crop already shown.
        self._selected_region: SelectedRegion | None = None
        # The geometry the lesson on screen was captured under, or None for a
        # full-screen or demo image. Bound together with the image when a
        # worker result is accepted, and handed to the overlay on every redraw.
        self._displayed_geometry: CaptureGeometry | None = None
        # The live selector and what to restore when it closes; None otherwise.
        self._selection: _SelectionSession | None = None
        self._refresh_voice_controls()

    # -- narration ---------------------------------------------------------

    def set_voice_enabled(self, enabled: bool) -> None:
        """Switches the voice on or off. Off stops; on only allows later steps."""
        if not enabled:
            self._expected_speech = None
        self.narration.set_enabled(bool(enabled))
        self._refresh_voice_controls()

    def replay_narration(self) -> None:
        """Reads the current step again from its stored text. No OCR, no model."""
        if not self._lesson_on_show():
            return
        self._silence_narration()
        self._speak_current_step()

    def stop_narration(self) -> None:
        """Stops reading; the step and its highlight stay exactly as they are."""
        self._silence_narration()
        self._refresh_voice_controls()

    def _silence_narration(self) -> None:
        """Stops playback and disowns it: no callback for it may act afterwards."""
        self._expected_speech = None
        self.narration.stop()

    def _lesson_on_show(self) -> bool:
        return bool(self.lesson_steps) and self.input_manager.current_state in (
            TutorState.TEACHING,
            TutorState.FINISHED,
        )

    def _speak_current_step(self) -> None:
        """Reads the displayed step's full stored explanation, if the voice is on."""
        self._expected_speech = None
        if self._lesson_on_show() and self.narration.enabled and not self.demo_manager.is_running:
            step = self.lesson_steps[self.current_step_index]
            self._expected_speech = self.narration.speak(
                str(step.get("explanation") or ""),
                lesson_id=self._displayed_lesson,
                step_index=self.current_step_index,
            )
        self._refresh_voice_controls()

    def _refresh_voice_controls(self, notice: str | None = None) -> None:
        # None keeps whatever note is showing; a failure reported inside
        # speak() must survive the refresh that follows it.
        if notice is None:
            notice = self.companion.lbl_voice_notice.text()
        self.companion.set_voice_controls(
            available=self.narration.available,
            enabled=self.narration.enabled,
            speaking=self.narration.is_speaking(),
            notice=notice,
            unavailable_reason=self.narration.unavailable_reason,
        )

    def _on_speech_started(self, token: NarrationToken) -> None:
        if token != self._expected_speech:
            return
        # The current step is being read, so an earlier failure note is over.
        self._refresh_voice_controls("")

    def _on_speech_finished(self, token: NarrationToken) -> None:
        if token != self._expected_speech:
            return
        # Natural completion only frees the controls. The step, its highlight
        # and the state are untouched: the learner presses Next.
        self._expected_speech = None
        self._refresh_voice_controls()

    def _on_speech_failed(self, token: NarrationToken, message: str) -> None:
        if token != self._expected_speech:
            return
        # The text lesson is unaffected; say so in the voice row, not a dialog.
        self._expected_speech = None
        self._refresh_voice_controls("Voice failed")

    def start(self):
        self.overlay.show()
        self.companion.show()
        if self.show_dev_panel:
            self.ui.show()
        else:
            logger.info("Developer panel hidden; run with --dev to show it.")
        self.hotkeys.start()

        # Only preload when a caller explicitly supplied an image. This used to
        # default to "sample2.png", a file that does not exist in the repo, so
        # every launch logged a FileNotFoundError and left ocr_data as None.
        if self.image_path:
            try:
                self.ocr_data = extract_ocr_data(self.image_path)
                self.overlay.set_background(self.image_path, show=False)
            except Exception as e:
                logger.error("Failed to load initial image %r: %s", self.image_path, e)
                self.image_path = None

    def ask(self, question):
        """Captures the screen and teaches an answer to this question.

        The single entry point for a question from anywhere -- the companion's
        field, the developer panel, or a script driving a demo. A blank
        question becomes the companion's default rather than a cancel.
        """
        self._pending_question = (question or "").strip() or DEFAULT_QUESTION
        self._question_armed_at = None
        self.input_manager.handle_action(InputAction.ASK)
        # Dispatch is synchronous, so the branch has read it by now. If the
        # guard dropped the action instead, nothing should linger for later.
        self._pending_question = ""

    def on_local_escape(self):
        """A Qt widget saw Escape.

        There is one authority for a physical Escape. While the global release
        hook is installed it delivers the same press as an ESCAPE action, and
        acting here too would abandon the draft and then dismiss the lesson
        behind it. Only when the hook is absent do the Qt events stand in.
        """
        if self.hotkeys.escape_hooked:
            return
        self.input_manager.handle_action(InputAction.ESCAPE)

    def _hotkey_armed(self) -> bool:
        return (
            self._question_armed_at is not None
            and time.monotonic() - self._question_armed_at < HOTKEY_ASK_WINDOW_SECONDS
        )

    def _drafting_question(self) -> bool:
        # A draft is only something Escape can abandon while a question could
        # be asked. While CAPTURING or ANALYZING the busy operation is what the
        # user sees, and a stale draft, focus or arming left in the hidden
        # companion must not swallow the Escape meant to cancel it.
        if self.input_manager.current_state not in (
            TutorState.IDLE,
            TutorState.TEACHING,
            TutorState.FINISHED,
        ):
            return False
        return (
            self._hotkey_armed()
            or self.companion.question_has_focus()
            or self.companion.has_draft_text()
        )

    def _on_escape(self):
        # The selector is what the learner is looking at. Escape closes it and
        # nothing else: the draft and any lesson behind it come back intact.
        if self._selection is not None:
            self._question_armed_at = None
            self._cancel_selection()
            return
        drafting = self._drafting_question()
        # Arming is dropped either way, and only after it has been consulted:
        # after an Escape the next hotkey press must focus the field, never
        # submit on the strength of an old press.
        self._question_armed_at = None
        # A request whose error dialog is open is what the learner is looking
        # at, so Escape retires it even over a stale draft in the companion.
        if self._modal_request is not None and self._is_current(self._modal_request):
            self._cancel_lesson()
            return
        if drafting:
            self.companion.abandon_draft()
            return
        if self.demo_manager.is_running or self.input_manager.current_state != TutorState.IDLE:
            self._cancel_lesson()

    def _cancel_lesson(self):
        if self._selection is not None:
            self._cancel_selection()
            return
        if self.demo_manager.is_running:
            self.demo_manager.stop_demo()
        self._abandon_request()
        self._clear_lesson()
        self.input_manager.set_state(TutorState.IDLE)
        self.ui.lbl_status.setText("Lesson cancelled. Ready.")

    # -- request identity --------------------------------------------------

    def _begin_request(self) -> int:
        """Retires whatever request owned the UI and returns the new owner."""
        self._request_serial += 1
        self._active_request = self._request_serial
        self._retire_in_flight_work()
        return self._active_request

    def _abandon_request(self) -> None:
        """Leaves no request owning the UI; in-flight results are dropped."""
        self._active_request = None
        self._retire_in_flight_work()

    def _retire_in_flight_work(self) -> None:
        self._pending_lesson = None
        if self.worker is not None:
            self.worker.requestInterruption()

    def _is_current(self, serial: int) -> bool:
        return self._active_request == serial

    def _owns_ui(self, request: LessonRequest) -> bool:
        """Whether a request's result may act: right session and still the owner."""
        return request.session_id == self.session.session_id and self._is_current(request.serial)

    # -- study session -----------------------------------------------------

    def start_new_session(self) -> None:
        """Forgets everything and starts over, keeping only the voice setting.

        Ownership goes first: the new session id and the cleared owner mean
        that anything an old worker or dialog reports from here on is
        recognised as belonging to a session that no longer exists, before
        any of the cleanup below can pump events. The worker itself is only
        asked to stop, never waited on; a question asked in the new session
        queues behind it exactly as after a cancel.
        """
        self._session_serial += 1
        self.session = StudySession(self._session_serial)
        self._abandon_request()
        self._modal_request = None
        self._question_armed_at = None

        self._silence_narration()
        if self._selection is not None:
            self._end_selection()
        self._interrupt_demo()

        self._clear_lesson()
        self.current_image = None
        self.image_path = None
        self.ocr_data = None
        self._selected_region = None
        self.companion.set_selected_area(None)
        self.companion.abandon_draft()
        self.companion.set_question("")
        self._pending_question = ""
        # set_state is silent when already IDLE, and the companion must
        # still drop a message or lesson text it was showing.
        self.input_manager.set_state(TutorState.IDLE)
        self.companion.apply_state(TutorState.IDLE)
        # The notice goes; narration.enabled is untouched, so the switch stays.
        self._refresh_voice_controls("")
        if self.worker is None:
            self.ui.btn_capture.setEnabled(True)
        self.ui.lbl_status.setText("New session. Ready.")

    def _on_input_action(self, action: InputAction):
        if action == InputAction.CAPTURE_SCREEN:
            # The hotkey puts the cursor in the question field. Pressed again
            # while the cursor is already there, it asks with whatever has
            # been typed, so hotkey-hotkey is the fastest path to a lesson.
            if self._hotkey_armed() or self.companion.question_has_focus():
                self.companion.submit_question()
            else:
                self.companion.focus_question()
                self._question_armed_at = time.monotonic()

        elif action == InputAction.ASK:
            question = self._pending_question
            self._pending_question = ""
            if self.ui.chk_fake_demo.isChecked():
                demo_id = self.ui.demo_dropdown.currentData()
                if demo_id:
                    self.ui.lbl_status.setText("Processing with AI...")
                    self.start_demo(demo_id)
                return

            # A prepared area is checked before anything is disturbed: a stale
            # one is refused outright rather than quietly becoming a
            # full-screen capture the learner did not ask for.
            selected = self._selected_region
            if selected is not None and not self._selection_still_valid(selected):
                self._report_stale_selection()
                return
            geometry = selected.geometry if selected is not None else None

            # A capture may be starting while a previous lesson is still shown.
            # Retire it first so the old highlights do not linger over the new
            # screenshot.
            self._clear_lesson()
            serial = self._begin_request()

            self.ui.lbl_status.setText("Capturing screen...")
            self.input_manager.set_state(TutorState.CAPTURING)

            # Step 1: Capture. Hiding the companion pumps the event loop, so a
            # cancel can land before, during or after the grab; the image is
            # kept local until this request is confirmed still to own the UI.
            try:
                image = self._capture_for(serial, geometry)
            except SelectionStaleError:
                if not self._is_current(serial):
                    return
                self.input_manager.set_state(TutorState.IDLE)
                self._report_stale_selection()
                return
            except Exception as e:
                logger.error("Capture error: %s", e)
                if not self._is_current(serial):
                    return
                # The request is over before the dialog opens. The dialog runs
                # its own event loop, and a newer request begun inside it must
                # not be reset to IDLE when the dialog closes.
                self.input_manager.set_state(TutorState.IDLE)
                self.ui.lbl_status.setText("Ready.")
                self._show_error("Couldn't capture screen.\nPlease try again.")
                return
            if image is None or not self._is_current(serial):
                logger.info("Lesson request %s cancelled during capture.", serial)
                return

            # Step 2: Analyze. OCR and Gemini both run on the worker thread,
            # so the UI stays responsive from here on.
            self.companion.set_question(question)
            self.input_manager.set_state(TutorState.ANALYZING)
            signature = selected.signature if selected is not None else None
            self.generate_lesson(
                LessonRequest(
                    serial,
                    question,
                    image,
                    geometry,
                    signature,
                    session_id=self.session.session_id,
                    history=self.session.snapshot(),
                )
            )

        elif action == InputAction.SELECT_REGION:
            self._start_selection()

        elif action == InputAction.CLEAR_REGION:
            self._clear_selected_region()

        elif action == InputAction.TOGGLE_DEBUG:
            self.toggle_debug()

        elif action == InputAction.NEXT_STEP:
            self.next_step()

        elif action == InputAction.PREV_STEP:
            self.prev_step()

        elif action == InputAction.CANCEL_LESSON:
            self._cancel_lesson()

        elif action == InputAction.ESCAPE:
            self._on_escape()

    def _tutor_panels(self) -> tuple[QWidget, ...]:
        """Every top-level window of ClickTutor's own that could land in a grab."""
        return (self.companion, self.ui, self.overlay)

    @contextmanager
    def _tutor_panels_hidden(self):
        """Takes every visible tutor panel off screen for the duration of a capture.

        The companion, the developer panel and the overlay are all always-on-
        top, so any of them lands in the screenshot and its own text reaches
        OCR. The model can then anchor a step on ClickTutor's UI rather than
        on the learner's code, and the highlight points at a panel instead of
        the thing being explained. Only the panels that were visible are
        hidden, and only those come back, so a developer panel the learner
        never opened stays closed.

        The brief sleep lets the compositor actually remove the windows before
        the grab; hiding and capturing in the same tick races on Windows.
        """
        hidden = [panel for panel in self._tutor_panels() if panel.isVisible()]
        if hidden:
            for panel in hidden:
                panel.hide()
            QApplication.processEvents()
            time.sleep(COMPOSITOR_SETTLE_SECONDS)
        try:
            yield
        finally:
            for panel in hidden:
                panel.show()

    def _capture_for(self, serial: int, geometry: CaptureGeometry | None = None):
        """Grabs the screen for a request, or returns None if it was cancelled first.

        Processing events while the panels hide is where a cancel can be
        dispatched synchronously, so ownership is checked again after that
        and before the grab. The caller checks once more before using the
        image, since the grab itself may pump events on some backends.

        Args:
            geometry: The prepared crop, or None for the overlay's whole screen.

        Raises:
            SelectionStaleError: The screen's signature changed while the
                panels were hiding or while the grab ran, so ``geometry``
                no longer describes what the learner selected.
        """
        with self._tutor_panels_hidden():
            if not self._is_current(serial):
                return None
            if geometry is None:
                return self.capture_engine.capture(region=self._overlay_screen_region())
            # The event pumping above is where a scale or resolution change
            # can land; the region is only exact for the screen it was built on.
            selected = self._selected_region
            if selected is None or not self._selection_still_valid(selected):
                raise SelectionStaleError("screen changed before the crop was grabbed")
            image = self.capture_engine.capture(region=geometry.region())
            if not self._selection_still_valid(selected):
                raise SelectionStaleError("screen changed while the crop was grabbed")
            return image

    def _clear_lesson(self):
        """Drops the current lesson and everything drawn for it."""
        self._silence_narration()
        self.lesson_steps = []
        self.current_step_index = 0
        self.is_debug_mode = False
        self._displayed_geometry = None
        self.overlay.clear()

    def _overlay_screen_region(self):
        """Physical-pixel bounds of the screen the overlay covers.

        Capture and overlay must describe the same area, otherwise highlights
        are placed against a region the user is not looking at. Returns None on
        WSL, where the PowerShell fallback always grabs the Windows primary
        screen and refuses any explicit region.
        """
        if not self.capture_engine.supports_regions():
            return None
        return physical_region(getattr(self.overlay, "screen_target", None))

    # -- area selection ----------------------------------------------------

    def _current_screen_signature(self) -> ScreenSignature | None:
        return screen_signature(getattr(self.overlay, "screen_target", None))

    def _selection_still_valid(self, selected: SelectedRegion) -> bool:
        return (
            self.capture_engine.supports_regions()
            and self._current_screen_signature() == selected.signature
        )

    def _request_matches_screen(self, request: LessonRequest) -> bool:
        """Whether a crop request's capture-time screen is still the screen on show.

        Compared against the request's own snapshot, never against whatever
        selection is current by the time the result lands: the learner may
        have cleared or replaced the area since, and that must not change
        where an already-captured crop is judged to belong.
        """
        if request.geometry is None:
            return True
        return self._current_screen_signature() == request.signature

    def _report_stale_selection(self) -> None:
        # The message replaces the step in the companion; reading on would
        # narrate something no longer on show.
        self._silence_narration()
        self.ui.lbl_status.setText(STALE_SELECTION_MESSAGE)
        self.companion.show_message(
            "AREA CHANGED", "Select the area again", STALE_SELECTION_MESSAGE
        )

    def _start_selection(self) -> None:
        """Opens the area selector over the overlay's screen.

        Only prepares a region: no capture, OCR or model call happens until
        the learner asks. The companion and overlay are hidden so the learner
        sees the screen they are selecting from, and come back afterwards.
        """
        if self._selection is not None:
            return
        if not self.capture_engine.supports_regions():
            self.ui.lbl_status.setText(SELECTION_UNAVAILABLE_MESSAGE)
            self.companion.show_message(
                "FULL SCREEN ONLY", "Area selection unavailable", SELECTION_UNAVAILABLE_MESSAGE
            )
            return
        screen = getattr(self.overlay, "screen_target", None)
        signature = self._current_screen_signature()
        if signature is None:
            # Also the case for an unplugged monitor whose QScreen wrapper is
            # already dead; asking it for its region would raise.
            logger.warning("No usable overlay screen to select on; ignoring SELECT_REGION.")
            return
        screen_region = physical_region(screen)
        if screen_region is None:
            logger.warning("No overlay screen region to select on; ignoring SELECT_REGION.")
            return

        # An offline demo's timer would otherwise flip the state under the
        # selector when its next step fires.
        self._interrupt_demo()
        # A worker still running for an older ask must not present a lesson,
        # open a dialog or re-enable anything while the selector is up. The
        # lesson already on screen is left as it is, silently: it comes back
        # unread and Replay reads it again on request.
        self._abandon_request()
        self._silence_narration()

        selector = RegionSelector(screen=screen)
        self._selection = _SelectionSession(
            selector=selector,
            signature=signature,
            screen_region=screen_region,
            prior_state=self.input_manager.current_state,
            draft=self.companion.draft_text(),
            companion_was_visible=self.companion.isVisible(),
            overlay_was_visible=self.overlay.isVisible(),
            dev_panel_was_visible=self.ui.isVisible(),
        )
        selector.selected.connect(
            lambda left, top, width, height: self._on_area_selected(
                selector, left, top, width, height
            )
        )
        selector.cancelled.connect(lambda: self._on_selection_cancelled(selector))
        selector.escape_pressed.connect(self.on_local_escape)

        self.input_manager.set_state(TutorState.SELECTING)
        self.ui.lbl_status.setText("Drag over the area to ask about. Esc cancels.")
        # The developer panel is always-on-top like the others and would sit
        # over the very screen the learner is trying to select from.
        for panel in self._tutor_panels():
            panel.hide()
        selector.begin()

    def _is_live_selector(self, selector: RegionSelector) -> bool:
        session = self._selection
        return (
            session is not None
            and session.selector is selector
            and self.input_manager.current_state is TutorState.SELECTING
        )

    def _on_area_selected(self, selector, left: int, top: int, width: int, height: int) -> None:
        if not self._is_live_selector(selector):
            logger.info("Dropping a selection from a retired selector.")
            return
        session = self._selection
        assert session is not None
        if self._current_screen_signature() != session.signature:
            # Measured against a screen that no longer exists in that form.
            self._end_selection()
            self._report_stale_selection()
            return
        try:
            geometry = CaptureGeometry.from_logical_rect(
                left,
                top,
                width,
                height,
                session.screen_region,
                session.signature.device_pixel_ratio,
            )
        except ValueError as exc:
            logger.info("Selection discarded: %s", exc)
            self._end_selection()
            return
        self._selected_region = SelectedRegion(geometry, session.signature)
        self._end_selection()
        self.ui.lbl_status.setText("Area selected. Ask a question to capture it.")

    def _on_selection_cancelled(self, selector) -> None:
        if not self._is_live_selector(selector):
            return
        self._end_selection()

    def _cancel_selection(self) -> None:
        if self._selection is None:
            return
        self._end_selection()
        self.ui.lbl_status.setText("Selection cancelled.")

    def _end_selection(self) -> None:
        """Takes the selector down and puts everything back as it was.

        The previously prepared region, if any, is kept unless the caller has
        just replaced it; the draft comes back verbatim; a lesson that was on
        screen is shown again with its own geometry untouched.
        """
        session = self._selection
        if session is None:
            return
        self._selection = None

        selector = session.selector
        # Disconnect before closing: close() reports a cancel, and a selector
        # that has already been retired must not be heard from again.
        for signal in (selector.selected, selector.cancelled, selector.escape_pressed):
            with suppress(TypeError):
                signal.disconnect()
        selector.dismiss()
        selector.deleteLater()

        if session.overlay_was_visible:
            self.overlay.show()
        if session.dev_panel_was_visible:
            self.ui.show()
        self.input_manager.set_state(session.prior_state)
        if self.lesson_steps and session.prior_state in (TutorState.TEACHING, TutorState.FINISHED):
            step = self.lesson_steps[self.current_step_index]
            self.companion.show_step(step, self.current_step_index, len(self.lesson_steps))
        self.companion.restore_draft(session.draft)
        self.companion.set_selected_area(
            self._selected_region.describe() if self._selected_region else None
        )
        if session.companion_was_visible:
            self.companion.show()
            self.companion.focus_question()
        if self.worker is None:
            self.ui.btn_capture.setEnabled(True)
        self._refresh_voice_controls()

    def _clear_selected_region(self) -> None:
        """Forgets the prepared area. The lesson on screen keeps its own crop."""
        self._selected_region = None
        self.companion.set_selected_area(None)
        self.ui.lbl_status.setText("Next capture uses the full screen.")

    def _install_lesson_background(self, show: bool) -> None:
        """Installs the displayed lesson's image under its own geometry."""
        self.overlay.set_background(
            self.image_path, show=show, capture_geometry=self._displayed_geometry
        )

    def _show_error(self, message):
        msg = QMessageBox(self.ui)
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setText(message)
        msg.setWindowTitle("ClickTutor")
        msg.exec()

    def generate_lesson(self, request: LessonRequest):
        """Starts lesson generation for a request, or queues it behind a stopping worker."""
        self.ui.btn_capture.setEnabled(False)
        if self.worker is not None:
            # The previous worker was cancelled but has not finished; only one
            # runs at a time. Any earlier pending request was already dropped
            # when this one began, so at most one is ever waiting.
            self._pending_lesson = request
            self.ui.lbl_status.setText("Waiting for the previous request to stop...")
            return
        self._start_worker(request)

    def _start_worker(self, request: LessonRequest):
        self.ui.lbl_status.setText("Reading the screen and asking Gemini...")
        worker = LessonWorker(request)
        # Queued explicitly. The signals are emitted on the worker thread and
        # every handler touches widgets, so each must run as an event on the
        # GUI thread, in emission order: a result always lands before the
        # finished signal that releases the worker.
        queued = Qt.ConnectionType.QueuedConnection
        worker.lesson_ready.connect(
            lambda ocr_data, steps, answer: self._on_worker_lesson_ready(
                worker, ocr_data, steps, answer
            ),
            queued,
        )
        worker.error.connect(lambda message: self._on_worker_error(worker, message), queued)
        worker.finished.connect(lambda: self._on_worker_finished(worker), queued)
        self.worker = worker
        worker.start()

    def _on_worker_finished(self, worker):
        """Releases a worker whose thread has ended and starts any queued request."""
        if worker is not self.worker:
            # Never true while one worker runs at a time; guarded so a late
            # signal can never release a newer worker or enable its controls.
            return
        # finished() is emitted just before the thread fully exits. The wait
        # covers that last stretch only -- the work is already done -- so the
        # thread is never destroyed while running.
        worker.wait()
        self.worker = None
        # The closures connected in _start_worker hold the worker, and with it
        # the request and its full-screen image, for as long as the QObject
        # lives. Deferred so any result already queued ahead of finished()
        # still reaches its handler before the object goes away.
        worker.deleteLater()

        pending, self._pending_lesson = self._pending_lesson, None
        if pending is not None and self._owns_ui(pending):
            self._start_worker(pending)
            return
        # A cancelled worker's finished() can land inside a newer request's
        # capture, before that request has recorded itself as pending. The
        # busy state then belongs to the newer request, which re-enables the
        # button itself when it settles.
        if self.input_manager.current_state in (
            TutorState.CAPTURING,
            TutorState.ANALYZING,
            TutorState.SELECTING,
        ):
            return
        self.ui.btn_capture.setEnabled(True)

    def _on_worker_lesson_ready(self, worker, ocr_data, steps, answer):
        """Accepts a result only if its request still owns the UI."""
        request = worker.request
        if not self._owns_ui(request):
            logger.info("Dropping lesson for retired request %s.", request.serial)
            return
        if not self._request_matches_screen(request):
            # The crop was grabbed on a screen that has since changed scale,
            # resolution or identity, so its geometry would place the boxes
            # against the wrong pixels. The result is dropped and the learner
            # asked to select again or clear; the prepared area is left for
            # them to decide, exactly as when the ask itself is refused.
            logger.info("Dropping lesson for request %s: the screen changed.", request.serial)
            self._abandon_request()
            self.ui.btn_capture.setEnabled(True)
            self.input_manager.set_state(TutorState.IDLE)
            self._report_stale_selection()
            return
        # Bind the lesson to the screen it was generated from before anything
        # is drawn: every box in these steps is in this image's pixels, and a
        # crop is placed at the origin it was grabbed from, not fitted.
        self.current_image = request.image
        self.image_path = request.image  # Provide back-compat
        self._displayed_geometry = request.geometry
        self.overlay.set_background(request.image, show=False, capture_geometry=request.geometry)
        self._on_lesson_finished(ocr_data, steps, answer)
        # Only a lesson that was actually presented becomes context for the
        # next question; an empty-step answer returned to IDLE above.
        if steps:
            self.session.record(request.question, str(answer or ""))

    def _on_worker_error(self, worker, error_msg):
        request = worker.request
        if not self._owns_ui(request):
            logger.info("Dropping error for retired request %s: %s", request.serial, error_msg)
            return
        self._on_lesson_error(error_msg, request.serial)

    def _on_lesson_finished(self, ocr_data, steps, answer):
        """Presents an accepted lesson. Callers have already checked ownership."""
        self.ui.btn_capture.setEnabled(True)
        self.ocr_data = ocr_data
        if not steps:
            self.ui.lbl_status.setText("Gemini didn't return any steps.")
            self.input_manager.set_state(TutorState.IDLE)
            return

        self.lesson_steps = steps
        self.current_step_index = 0
        self.is_debug_mode = False
        self._displayed_lesson += 1
        self._install_lesson_background(show=False)
        self.input_manager.set_state(TutorState.TEACHING)
        self.show_current_step()
        self.ui.lbl_status.setText("Lesson ready! Use Next/Prev to navigate.")
        # A new lesson starts with a clean voice row: an earlier "Voice
        # failed" belonged to a lesson no longer on show.
        self._refresh_voice_controls("")
        # Spoken only once the step and its highlight are on screen.
        self._speak_current_step()

    def _on_lesson_error(self, error_msg, serial=None):
        """Reports a failed lesson. `serial` is the request it belongs to, if known.

        Both dialogs below run nested event loops, during which the learner
        can cancel or ask again. Nothing after a dialog returns may act unless
        the request still owns the UI.
        """
        self._silence_narration()
        self.ui.btn_capture.setEnabled(True)
        self.input_manager.set_state(TutorState.IDLE)
        self.ui.lbl_status.setText("Ready.")

        # An unreadable screen is not a network fault, so offering Demo Mode
        # would be a non-sequitur. Say what actually happened.
        if error_msg == UNREADABLE_SCREEN_MESSAGE:
            self.companion.show_message(
                "CAN'T READ",
                "No readable text on that screen",
                "Try capturing an area with clearer text, such as code or documentation.",
            )
            self._show_error(error_msg)
            return

        # Graceful Failure: Network or Gemini issue
        self._modal_request = serial
        try:
            reply = QMessageBox.question(
                self.ui,
                "Network Error",
                "Network unavailable or API error. Run Demo Mode instead?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
        finally:
            # Only this dialog's own claim is released. A newer request that
            # opened its own dialog inside this one has already replaced it,
            # and an older, retired owner must not come back when it closes.
            if self._modal_request == serial:
                self._modal_request = None
        if serial is not None and not self._is_current(serial):
            logger.info("Request %s retired while its error dialog was open.", serial)
            return
        if reply == QMessageBox.StandardButton.Yes:
            # Fallback to a demo if available
            demos = self.demo_manager.get_available_demos()
            if demos:
                demo_id = list(demos.keys())[0]
                self.start_demo(demo_id)
        else:
            self._show_error(f"Error: {error_msg}")

    def show_current_step(self):
        self._interrupt_demo()
        if not self.lesson_steps:
            return

        # Interrupting a demo above resets the state to IDLE, so re-assert
        # TEACHING whenever a real lesson step is actually on screen.
        self.input_manager.set_state(TutorState.TEACHING)

        step = self.lesson_steps[self.current_step_index]
        self.companion.show_step(step, self.current_step_index, len(self.lesson_steps))
        self._render_box(self._step_box(step, self.ocr_data), step)

    def _step_box(self, step, ocr_data):
        """The box to draw for a step, in captured-image pixels, or None.

        A step that came through LessonEngine carries its routed result under
        STEP_LOCATION_KEY, and that record is final: trusted OCR and vision
        boxes are drawn as stored, and a stored None stays blank even if the
        anchor would match here. Looking the anchor up again was how rejected
        word-level matches reappeared on screen, and it would also mean a
        Next or Previous press could trigger a new lookup.

        Only steps without the key -- offline demos parsed straight from
        lesson.json -- are searched, and through a locator that applies the
        same phrase-level trust boundary.
        """
        if STEP_LOCATION_KEY in step:
            stored = step[STEP_LOCATION_KEY]
            return stored["box"] if stored else None

        location = self.locator.locate(ocr_data, step["anchor"], step.get("context"))
        return location.box if location else None

    def _render_box(self, box, step):
        if box:
            # OCR reports boxes in captured-image pixels. The overlay is
            # measured in logical widget pixels, which differ under OS display
            # scaling and when a demo screenshot's resolution is not the
            # screen's. Convert before building any shape; the label geometry
            # below is then computed in widget space, where the font lives.
            box = self.overlay.mapper.map_box(box)

            attention_type = step.get("attention", "rectangle")

            shape = None
            if attention_type == "circle":
                shape = CircleShape(
                    x=box["left"], y=box["top"], width=box["width"], height=box["height"]
                )
            elif attention_type == "underline":
                shape = UnderlineShape(
                    x=box["left"], y=box["top"], width=box["width"], height=box["height"]
                )
            else:
                # The prompt offers "arrow", the validator accepts it, and the
                # renderer has no arrow shape -- relationship arrows are Phase 4
                # work. Such a step silently became a rectangle, so say so.
                if attention_type not in RENDERABLE_ATTENTIONS:
                    logger.warning(
                        "Step %s requested attention %r, which cannot be drawn yet; "
                        "falling back to a rectangle.",
                        step.get("step"),
                        attention_type,
                    )
                shape = RectangleShape(
                    x=box["left"], y=box["top"], width=box["width"], height=box["height"]
                )

            # No caption on the overlay. It sat directly above the highlight and
            # therefore on top of whatever was there -- in a recorded lesson it
            # covered the sentence the step was teaching. The companion already
            # shows the step number, title and explanation, so the overlay's job
            # is only to point.
            self.overlay.set_shapes([shape])
        else:
            # "NONE" is the model correctly declining to anchor a step that has
            # no on-screen referent, so it is expected rather than a failure.
            # Anything else reaching here is an anchor that could not be
            # located even after repair, which is worth knowing about.
            anchor = (step.get("anchor") or "").strip().upper()
            if anchor and anchor != "NONE":
                logger.warning(
                    "Step %s: anchor %r could not be located on screen; "
                    "showing this step without a highlight.",
                    step.get("step"),
                    step.get("anchor"),
                )
            self.overlay.set_shapes([])

    def _interrupt_demo(self):
        if self.demo_manager.is_running:
            self.demo_manager.stop_demo()

    def start_demo(self, demo_id):
        # An offline demo takes over the overlay, so a lesson still being
        # generated must not land on top of it when its worker reports back.
        self._abandon_request()
        self._silence_narration()
        self.ui.lbl_status.setText(f"Playing Demo: {demo_id}")
        self.demo_manager.start_demo(demo_id)

    def start_recording(self, demo_id):
        self.is_recording_mode = True
        self.recorder.start_recording()
        self.start_demo(demo_id)

    def _on_demo_started(self, image_path):
        self._silence_narration()
        self.is_debug_mode = False
        # A demo screenshot is fitted across the overlay, never cropped.
        self._displayed_geometry = None
        # A running demo is a lesson as far as input routing is concerned.
        # While this stayed IDLE, the CANCEL_LESSON guard dropped Esc and a
        # demo could not be interrupted by the user at all.
        self.input_manager.set_state(TutorState.TEACHING)
        self.overlay.set_background(image_path, show=True)

    def _on_demo_stopped(self):
        self.overlay.set_shapes([])
        self.input_manager.set_state(TutorState.IDLE)

        if self.is_recording_mode:
            self.ui.lbl_status.setText("Compiling MP4... Please wait.")
            self.recorder.stop_recording("demo_output.mp4")
            self.is_recording_mode = False
        else:
            self.ui.lbl_status.setText("Demo stopped. Ready.")

    def _on_recording_finished(self, path):
        self.ui.lbl_status.setText(f"MP4 saved to {path}! Ready.")
        self.overlay.clear()

    def _on_demo_step_changed(self, ocr_data, step_data):
        self._render_box(self._step_box(step_data, ocr_data), step_data)

    def next_step(self):
        self._interrupt_demo()
        if self.lesson_steps and self.current_step_index < len(self.lesson_steps) - 1:
            self._move_to_step(self.current_step_index + 1)

    def prev_step(self):
        self._interrupt_demo()
        if self.lesson_steps and self.current_step_index > 0:
            self._move_to_step(self.current_step_index - 1)

    def _move_to_step(self, index: int) -> None:
        """A genuine step change: the old reading stops first, the new one follows.

        A press at either end never reaches here, so it neither restarts nor
        interrupts what is being read.
        """
        self._silence_narration()
        self.current_step_index = index
        self.show_current_step()
        self._speak_current_step()

    def toggle_debug(self):
        self._interrupt_demo()

        # Debug mode draws every OCR word, so it needs a capture to draw from.
        # Without this guard build_words(None) raised AttributeError inside a
        # Qt slot, which is unrecoverable and left the state machine stuck.
        if not self.is_debug_mode and not self.ocr_data:
            logger.info("Debug overlay requested before any capture; ignoring.")
            self.ui.lbl_status.setText("Nothing to debug yet — capture a screen first.")
            return

        self.is_debug_mode = not self.is_debug_mode

        if self.is_debug_mode:
            # The word boxes replace the step's highlight; leaving it comes
            # back through show_current_step(), which is silent by design.
            self._silence_narration()
            self._refresh_voice_controls()
            self._install_lesson_background(show=True)
            words = build_words(self.ocr_data, min_confidence=0)
            ocr_scale = self.ocr_data.get("_scale", 1)
            mapper = self.overlay.mapper
            shapes = []
            for w in words:
                # Two separate corrections: undo the OCR upscale to get back to
                # captured-image pixels, then map those onto the widget.
                box = mapper.map_box(
                    {
                        "left": w["left"] / ocr_scale,
                        "top": w["top"] / ocr_scale,
                        "width": w["width"] / ocr_scale,
                        "height": w["height"] / ocr_scale,
                    }
                )

                shapes.append(
                    DebugBoxShape(
                        x=box["left"],
                        y=box["top"],
                        width=box["width"],
                        height=box["height"],
                        text=w["raw_text"],
                        confidence=w["confidence"],
                        # Labelling every word buried the useful signal: a
                        # dense screen produced ~300 overlapping labels. The
                        # low-confidence reads are the ones worth inspecting,
                        # because those are where a missed anchor comes from.
                        show_label=w["confidence"] < DEBUG_LABEL_CONFIDENCE,
                    )
                )
            labelled = sum(1 for s in shapes if s.show_label)
            logger.info(
                "Debug overlay: %s words, %s labelled (confidence < %s)",
                len(shapes),
                labelled,
                DEBUG_LABEL_CONFIDENCE,
            )
            self.ui.lbl_status.setText(
                f"Debug: {len(shapes)} words, {labelled} low-confidence labelled."
            )
            self.overlay.set_shapes(shapes)
        else:
            self.overlay.clear()
            if self.lesson_steps:
                # clear() dropped the crop placement with the pixmap; put the
                # lesson's own image back, hidden, so its boxes map as before.
                self._install_lesson_background(show=False)
                self.show_current_step()
