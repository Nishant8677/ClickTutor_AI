import sys
from PyQt6.QtWidgets import QApplication, QWidget, QPushButton, QVBoxLayout, QTextEdit, QLabel, QHBoxLayout
from PyQt6.QtCore import Qt
from src.attention.overlay import TransparentOverlay
from src.attention.shapes import RectangleShape, CircleShape, UnderlineShape, LabelShape, DebugBoxShape
from src.ocr_locator import extract_ocr_data, build_words, find_text
from src.lesson_engine import parse_lesson_steps

class DesktopUI(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.setWindowTitle("ClickTutor")
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint)
        self.resize(350, 300)
        
        layout = QVBoxLayout()
        
        self.lbl_status = QLabel("Ready. Ask a question about the screen:")
        layout.addWidget(self.lbl_status)
        
        self.text_question = QTextEdit()
        self.text_question.setPlaceholderText("e.g. What does count do?")
        self.text_question.setMaximumHeight(80)
        layout.addWidget(self.text_question)
        
        self.btn_ask = QPushButton("Ask Tutor")
        self.btn_ask.clicked.connect(self.on_ask)
        layout.addWidget(self.btn_ask)
        
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

    def on_ask(self):
        question = self.text_question.toPlainText().strip()
        if question:
            self.lbl_status.setText("Generating lesson...")
            self.controller.generate_lesson(question)
            self.lbl_status.setText("Lesson ready! Use Next/Prev to navigate.")
            
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_F8:
            self.controller.toggle_debug()
        else:
            super().keyPressEvent(event)

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

class DesktopController:
    def __init__(self, default_image="sample2.png"):
        self.image_path = default_image
        self.ocr_data = None
        
        self.overlay = TransparentOverlay()
        self.ui = DesktopUI(self)
        
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

    def toggle_debug(self):
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
            self.overlay.set_shapes([])
            if self.lesson_steps:
                self.show_current_step()

    def generate_lesson(self, question):
        self.ui.btn_ask.setEnabled(False)
        self.ui.lbl_status.setText("Asking Gemini for a lesson... please wait!")
        
        self.worker = LessonWorker(self.image_path, self.ocr_data, question)
        self.worker.finished.connect(self._on_lesson_finished)
        self.worker.error.connect(self._on_lesson_error)
        self.worker.start()

    def _on_lesson_finished(self, steps, answer):
        self.ui.btn_ask.setEnabled(True)
        if not steps:
            self.ui.lbl_status.setText("Gemini didn't return any steps.")
            return
            
        self.lesson_steps = steps
        self.current_step_index = 0
        self.is_debug_mode = False
        self.overlay.set_background(self.image_path, show=True)
        self.show_current_step()
        self.ui.lbl_status.setText("Lesson ready! Use Next/Prev to navigate.")

    def _on_lesson_error(self, error_msg):
        self.ui.btn_ask.setEnabled(True)
        self.ui.lbl_status.setText(f"Error: {error_msg}")

    def show_current_step(self):
        if not self.lesson_steps:
            return
            
        step = self.lesson_steps[self.current_step_index]
        box = find_text(self.ocr_data, step["anchor"], step["context"])
        
        if box:
            attention_type = step.get("attention", "rectangle")
            
            shape = None
            if attention_type == "circle":
                shape = CircleShape(x=box["left"], y=box["top"], width=box["width"], height=box["height"])
            elif attention_type == "underline":
                shape = UnderlineShape(x=box["left"], y=box["top"], width=box["width"], height=box["height"])
            else:
                shape = RectangleShape(x=box["left"], y=box["top"], width=box["width"], height=box["height"])
                
            shapes = [
                shape,
                LabelShape(
                    x=box["left"], y=max(0, box["top"] - 40),
                    width=250, height=30,
                    text=f"Step {step['step']}: {step['title']}",
                    bg_color="white", text_color="black"
                )
            ]
            self.overlay.set_shapes(shapes)
        else:
            self.overlay.set_shapes([])

    def next_step(self):
        if self.lesson_steps and self.current_step_index < len(self.lesson_steps) - 1:
            self.current_step_index += 1
            self.show_current_step()

    def prev_step(self):
        if self.lesson_steps and self.current_step_index > 0:
            self.current_step_index -= 1
            self.show_current_step()
