import logging
from PyQt6.QtWidgets import QApplication, QWidget, QPushButton, QVBoxLayout, QTextEdit, QLabel, QHBoxLayout, QComboBox, QCheckBox, QInputDialog, QMessageBox
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFontMetrics, QFont
from src.attention.overlay import TransparentOverlay
from src.attention.shapes import RectangleShape, CircleShape, UnderlineShape, LabelShape, DebugBoxShape
from src.ocr_locator import extract_ocr_data, build_words, find_text
from src.input import InputManager, TutorState, InputAction

logger = logging.getLogger(__name__)

class LessonWorker(QThread):
    finished = pyqtSignal(list, str)
    error = pyqtSignal(str)

    def __init__(self, image_path, ocr_data, question):
        super().__init__()
        self.image_path = image_path
        self.ocr_data = ocr_data
        self.question = question

    def run(self):
        try:
            from src.lesson_engine import LessonEngine
            engine = LessonEngine(self.image_path, self.ocr_data)
            answer, _, steps = engine.generate_lesson(self.question, [], "")
            self.finished.emit(steps, answer)
        except Exception as e:
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
        self.chk_fake_demo.setToolTip("If checked, 'Capture & Ask' will play the selected offline demo instead of calling AI.")
        demo_layout.addWidget(self.chk_fake_demo)
        
        layout.addLayout(demo_layout)
        
        self.lbl_status = QLabel("Ready. Ask a question about the screen:")
        layout.addWidget(self.lbl_status)
        
        self.text_question = QTextEdit()
        self.text_question.setPlaceholderText("e.g. What does count do?")
        self.text_question.setMaximumHeight(80)
        layout.addWidget(self.text_question)
        
        self.btn_capture = QPushButton("Capture & Ask")
        self.btn_capture.clicked.connect(lambda: self.controller.input_manager.handle_action(InputAction.CAPTURE_SCREEN))
        layout.addWidget(self.btn_capture)
        
        nav_layout = QHBoxLayout()
        self.btn_prev = QPushButton("< Previous Step")
        self.btn_prev.clicked.connect(self.controller.prev_step)
        nav_layout.addWidget(self.btn_prev)
        
        self.btn_next = QPushButton("Next Step >")
        self.btn_next.clicked.connect(self.controller.next_step)
        nav_layout.addWidget(self.btn_next)
        
        layout.addLayout(nav_layout)
        
        self.btn_debug = QPushButton("Toggle OCR Debug Mode (F8)")
        self.btn_debug.clicked.connect(lambda: self.controller.input_manager.handle_action(InputAction.TOGGLE_DEBUG))
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
            
    def on_capture_ask(self):
        question = self.text_question.toPlainText().strip()
        if question:
            if self.chk_fake_demo.isChecked():
                # Fake AI mode for video recording! Play the selected demo instead.
                demo_id = self.demo_dropdown.currentData()
                if demo_id:
                    self.lbl_status.setText("Processing with AI...")
                    self.controller.start_demo(demo_id)
            else:
                # Real AI mode
                self.lbl_status.setText("Capturing screen...")
                self.controller.capture_and_generate(question)
            
    def keyPressEvent(self, event):
        # Interrupt demo on any key press in UI
        if self.controller.demo_manager.is_running:
            self.controller.input_manager.handle_action(InputAction.CANCEL_LESSON)
            
        if event.key() == Qt.Key.Key_F8:
            self.controller.input_manager.handle_action(InputAction.TOGGLE_DEBUG)
        elif event.key() == Qt.Key.Key_Escape:
            self.controller.input_manager.handle_action(InputAction.CANCEL_LESSON)
        else:
            super().keyPressEvent(event)
            
    def mousePressEvent(self, event):
        # Interrupt demo on any mouse click in UI
        if self.controller.demo_manager.is_running:
            self.controller.input_manager.handle_action(InputAction.CANCEL_LESSON)
        super().mousePressEvent(event)

class DesktopController:
    def __init__(self, default_image="sample2.png"):
        from src.capture import ScreenCapture
        from src.desktop.demo_manager import DemoManager
        from src.desktop.recorder import Mp4Recorder
        from src.input.hotkeys import HotkeyManager
        
        self.image_path = default_image
        self.ocr_data = None
        
        self.input_manager = InputManager()
        self.input_manager.add_listener(self._on_input_action)
        
        self.hotkeys = HotkeyManager()
        self.hotkeys.action_triggered.connect(self.input_manager.handle_action)
        
        # Ensure hotkeys are unregistered when the application closes
        if QApplication.instance():
            QApplication.instance().aboutToQuit.connect(self.hotkeys.stop)
        
        self.overlay = TransparentOverlay()
        self.ui = DesktopUI(self)
        
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
        self.worker = None

    def start(self):
        self.overlay.show()
        self.ui.show()
        self.hotkeys.start()
        
        try:
            self.ocr_data = extract_ocr_data(self.image_path)
            self.overlay.set_background(self.image_path, show=False)
        except Exception as e:
            logger.error("Failed to load initial OCR data: %s", e)

    def _on_input_action(self, action: InputAction):
        if action == InputAction.CAPTURE_SCREEN:
            if self.ui.chk_fake_demo.isChecked():
                demo_id = self.ui.demo_dropdown.currentData()
                if demo_id:
                    self.ui.lbl_status.setText("Processing with AI...")
                    self.start_demo(demo_id)
                return
                
            self.ui.lbl_status.setText("Capturing screen...")
            self.input_manager.set_state(TutorState.CAPTURING)
            
            # Step 1: Capture
            try:
                self.current_image = self.capture_engine.capture(
                    region=self._overlay_screen_region()
                )
                self.image_path = self.current_image # Provide back-compat
                # Every OCR box from this capture is measured against these
                # dimensions, so the overlay needs them to place highlights.
                self.overlay.set_source_size(
                    self.current_image.width, self.current_image.height
                )
            except Exception as e:
                logger.error("Capture error: %s", e)
                self._show_error("Couldn't capture screen.\nPlease try again.")
                self.input_manager.set_state(TutorState.IDLE)
                self.ui.lbl_status.setText("Ready.")
                return
                
            # Step 2: Question Popup
            question, ok = QInputDialog.getText(self.ui, "ClickTutor", "Ask a question about the screen:")
            if not ok or not question.strip():
                self.input_manager.set_state(TutorState.IDLE)
                self.ui.lbl_status.setText("Ready.")
                return
                
            # Step 3: Analyze
            self.input_manager.set_state(TutorState.ANALYZING)
            
            try:
                self.ocr_data = extract_ocr_data(self.current_image)
            except Exception as e:
                logger.error("OCR extraction failed: %s", e)
                # Graceful Failure: Empty screen / OCR failed
                self._show_error("Couldn't precisely locate the text.\nPlease try capturing a different area.")
                self.input_manager.set_state(TutorState.IDLE)
                self.ui.lbl_status.setText("Ready.")
                return
                
            self.generate_lesson(question)
                
        elif action == InputAction.TOGGLE_DEBUG:
            self.toggle_debug()
            
        elif action == InputAction.CANCEL_LESSON:
            if self.demo_manager.is_running:
                self.demo_manager.stop_demo()
            self.input_manager.set_state(TutorState.IDLE)
            self.overlay.set_shapes([])
            self.ui.lbl_status.setText("Lesson cancelled. Ready.")

    def _overlay_screen_region(self):
        """Physical-pixel bounds of the screen the overlay covers.

        Capture and overlay must describe the same area, otherwise highlights
        are placed against a region the user is not looking at. Returns None on
        WSL, where the PowerShell fallback always grabs the Windows primary
        screen and cannot honour a region anyway.

        Approximate on mixed-DPI multi-monitor setups: Qt normalises logical
        coordinates across screens, so the origin is not a uniform multiple of
        the device pixel ratio there.
        """
        screen = getattr(self.overlay, "screen_target", None)
        if screen is None:
            return None

        geometry = screen.geometry()
        ratio = screen.devicePixelRatio()
        return {
            "left": round(geometry.x() * ratio),
            "top": round(geometry.y() * ratio),
            "width": round(geometry.width() * ratio),
            "height": round(geometry.height() * ratio),
        }

    def _show_error(self, message):
        msg = QMessageBox(self.ui)
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setText(message)
        msg.setWindowTitle("ClickTutor")
        msg.exec()

    def generate_lesson(self, question):
        self.ui.btn_capture.setEnabled(False)
        self.ui.lbl_status.setText("Asking Gemini for a lesson... please wait!")
        
        self.worker = LessonWorker(self.image_path, self.ocr_data, question)
        self.worker.finished.connect(self._on_lesson_finished)
        self.worker.error.connect(self._on_lesson_error)
        self.worker.start()

    def _on_lesson_finished(self, steps, answer):
        self.ui.btn_capture.setEnabled(True)
        if not steps:
            self.ui.lbl_status.setText("Gemini didn't return any steps.")
            self.input_manager.set_state(TutorState.IDLE)
            return
            
        self.lesson_steps = steps
        self.current_step_index = 0
        self.is_debug_mode = False
        self.overlay.set_background(self.image_path, show=False)
        self.input_manager.set_state(TutorState.TEACHING)
        self.show_current_step()
        self.ui.lbl_status.setText("Lesson ready! Use Next/Prev to navigate.")

    def _on_lesson_error(self, error_msg):
        self.ui.btn_capture.setEnabled(True)
        self.input_manager.set_state(TutorState.IDLE)
        self.ui.lbl_status.setText("Ready.")
        
        # Graceful Failure: Network or Gemini issue
        reply = QMessageBox.question(
            self.ui, 'Network Error',
            "Network unavailable or API error. Run Demo Mode instead?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes
        )
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
            
        step = self.lesson_steps[self.current_step_index]
        box = find_text(self.ocr_data, step["anchor"], step["context"])
        self._render_box(box, step)

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
                shape = CircleShape(x=box["left"], y=box["top"], width=box["width"], height=box["height"])
            elif attention_type == "underline":
                shape = UnderlineShape(x=box["left"], y=box["top"], width=box["width"], height=box["height"])
            else:
                shape = RectangleShape(x=box["left"], y=box["top"], width=box["width"], height=box["height"])
                
            label_text = f"Step {step['step']}: {step['title']}"
            font = QFont("Arial", 16, QFont.Weight.Bold)
            fm = QFontMetrics(font)
            text_width = fm.horizontalAdvance(label_text) + 30
            text_height = fm.height() + 10
            
            shapes = [
                shape,
                LabelShape(
                    x=box["left"], y=max(0, box["top"] - text_height - 10),
                    width=text_width, height=text_height,
                    text=label_text,
                    bg_color="white", text_color="black"
                )
            ]
            self.overlay.set_shapes(shapes)
        else:
            self.overlay.set_shapes([])

    def _interrupt_demo(self):
        if self.demo_manager.is_running:
            self.demo_manager.stop_demo()

    def start_demo(self, demo_id):
        self.ui.lbl_status.setText(f"Playing Demo: {demo_id}")
        self.demo_manager.start_demo(demo_id)
        
    def start_recording(self, demo_id):
        self.is_recording_mode = True
        self.recorder.start_recording()
        self.start_demo(demo_id)

    def _on_demo_started(self, image_path):
        self.is_debug_mode = False
        self.overlay.set_background(image_path, show=True)

    def _on_demo_stopped(self):
        self.overlay.set_shapes([])
        
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
        box = find_text(ocr_data, step_data["anchor"], step_data["context"])
        self._render_box(box, step_data)

    def next_step(self):
        self._interrupt_demo()
        if self.lesson_steps and self.current_step_index < len(self.lesson_steps) - 1:
            self.current_step_index += 1
            self.show_current_step()

    def prev_step(self):
        self._interrupt_demo()
        if self.lesson_steps and self.current_step_index > 0:
            self.current_step_index -= 1
            self.show_current_step()

    def toggle_debug(self):
        self._interrupt_demo()
        self.is_debug_mode = not self.is_debug_mode
        
        if self.is_debug_mode:
            self.overlay.set_background(self.image_path, show=True)
            words = build_words(self.ocr_data, min_confidence=0)
            ocr_scale = self.ocr_data.get("_scale", 1)
            mapper = self.overlay.mapper
            shapes = []
            for w in words:
                # Two separate corrections: undo the OCR upscale to get back to
                # captured-image pixels, then map those onto the widget.
                box = mapper.map_box({
                    "left": w["left"] / ocr_scale,
                    "top": w["top"] / ocr_scale,
                    "width": w["width"] / ocr_scale,
                    "height": w["height"] / ocr_scale,
                })

                shapes.append(
                    DebugBoxShape(
                        x=box["left"], y=box["top"],
                        width=box["width"], height=box["height"],
                        text=w["raw_text"],
                        confidence=w["confidence"]
                    )
                )
            self.overlay.set_shapes(shapes)
        else:
            self.overlay.set_background(None, show=False)
            self.overlay.clear()
            if self.lesson_steps:
                self.show_current_step()

