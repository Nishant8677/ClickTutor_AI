from PyQt6.QtWidgets import QApplication, QWidget, QPushButton, QVBoxLayout, QTextEdit, QLabel, QHBoxLayout, QComboBox, QCheckBox
from PyQt6.QtCore import Qt
from src.attention.overlay import TransparentOverlay
from src.attention.shapes import RectangleShape, CircleShape, UnderlineShape, LabelShape, DebugBoxShape
from src.ocr_locator import extract_ocr_data, build_words, find_text
from src.lesson_engine import parse_lesson_steps

from PyQt6.QtCore import Qt, QThread, pyqtSignal

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
        self.btn_capture.clicked.connect(self.on_capture_ask)
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
        self.btn_debug.clicked.connect(self.controller.toggle_debug)
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
            self.controller.demo_manager.stop_demo()
            
        if event.key() == Qt.Key.Key_F8:
            self.controller.toggle_debug()
        else:
            super().keyPressEvent(event)
            
    def mousePressEvent(self, event):
        # Interrupt demo on any mouse click in UI
        if self.controller.demo_manager.is_running:
            self.controller.demo_manager.stop_demo()
        super().mousePressEvent(event)

class DesktopController:
    def __init__(self, default_image="sample2.png"):
        from src.desktop.capture import CaptureEngine
        from src.desktop.demo_manager import DemoManager
        from src.desktop.recorder import Mp4Recorder
        
        self.image_path = default_image
        self.ocr_data = None
        
        self.overlay = TransparentOverlay()
        self.ui = DesktopUI(self)
        
        self.capture_engine = CaptureEngine()
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
        
        print(f"Controller: Loading OCR data for {self.image_path}")
        try:
            self.ocr_data = extract_ocr_data(self.image_path)
            self.overlay.set_background(self.image_path, show=False)
        except Exception as e:
            print(f"Error loading initial OCR: {e}")

    def capture_and_generate(self, question):
        self._interrupt_demo()
        try:
            # Phase 3 auto-capture
            new_image = self.capture_engine.capture(target="screen")
            self.image_path = new_image
            self.ocr_data = extract_ocr_data(self.image_path)
            self.generate_lesson(question)
        except Exception as e:
            self.ui.lbl_status.setText(f"Capture error: {e}")

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
            return
            
        self.lesson_steps = steps
        self.current_step_index = 0
        self.is_debug_mode = False
        self.overlay.set_background(self.image_path, show=False) # Only overlay the shapes
        self.show_current_step()
        self.ui.lbl_status.setText("Lesson ready! Use Next/Prev to navigate.")

    def _on_lesson_error(self, error_msg):
        self.ui.btn_capture.setEnabled(True)
        self.ui.lbl_status.setText(f"Error: {error_msg}")

    def show_current_step(self):
        self._interrupt_demo()
        if not self.lesson_steps:
            return
            
        step = self.lesson_steps[self.current_step_index]
        box = find_text(self.ocr_data, step["anchor"], step["context"])
        self._render_box(box, step)

    def _render_box(self, box, step):
        if box:
            attention_type = step.get("attention", "rectangle")
            
            shape = None
            if attention_type == "circle":
                shape = CircleShape(x=box["left"], y=box["top"], width=box["width"], height=box["height"])
            elif attention_type == "underline":
                shape = UnderlineShape(x=box["left"], y=box["top"], width=box["width"], height=box["height"])
            else:
                shape = RectangleShape(x=box["left"], y=box["top"], width=box["width"], height=box["height"])
                
            from PyQt6.QtGui import QFontMetrics, QFont
            label_text = f"Step {step['step']}: {step['title']}"
            font = QFont("Arial", 16, QFont.Weight.Bold)
            fm = QFontMetrics(font)
            # Add some padding around the text
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
        # We can implement full presentation mode hiding here in the future
        # self.ui.hide()

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
            scale = self.ocr_data.get("_scale", 1)
            shapes = []
            for w in words:
                left = round(w["left"] / scale)
                top = round(w["top"] / scale)
                width = max(1, round(w["width"] / scale))
                height = round(w["height"] / scale)
                
                shapes.append(
                    DebugBoxShape(
                        x=left, y=top, width=width, height=height,
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

