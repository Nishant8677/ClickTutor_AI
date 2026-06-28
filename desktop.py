import sys
from PyQt6.QtWidgets import QApplication, QWidget, QPushButton, QVBoxLayout
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QPixmap
from src.attention.shapes import RectangleShape, CircleShape, UnderlineShape, LabelShape, DebugBoxShape
from src.attention.renderer import Renderer
from src.ocr_locator import extract_ocr_data, build_words

class OverlayPoC(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.WindowTransparentForInput |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        screen_geometry = QApplication.primaryScreen().geometry()
        self.setGeometry(screen_geometry)
        
        # Default mode shapes
        self.normal_shapes = [
            RectangleShape(x=200, y=200, width=150, height=50),
            CircleShape(x=475, y=200, width=50, height=50),
            UnderlineShape(x=800, y=260, width=150, height=5),
            LabelShape(x=200, y=400, width=200, height=40, text="ClickTutor Label")
        ]
        
        self.debug_shapes = []
        self.is_debug_mode = False
        self.test_image_pixmap = None
        self.show_test_image = False

    def load_debug_data(self):
        print("Loading OCR data for debug mode...")
        # Get image path from command line or use a default
        image_path = "sample2.png"
        if len(sys.argv) > 1:
            image_path = sys.argv[1]
            
        print(f"Running OCR on: {image_path}")
        try:
            self.test_image_pixmap = QPixmap(image_path)
            ocr_data = extract_ocr_data(image_path)
            words = build_words(ocr_data, min_confidence=0)
            scale = ocr_data.get("_scale", 1)
            
            self.debug_shapes.clear()
            for w in words:
                left = round(w["left"] / scale)
                top = round(w["top"] / scale)
                width = max(1, round(w["width"] / scale))
                height = round(w["height"] / scale)
                
                self.debug_shapes.append(
                    DebugBoxShape(
                        x=left, y=top, width=width, height=height,
                        text=w["raw_text"],
                        confidence=w["confidence"]
                    )
                )
            print(f"Loaded {len(self.debug_shapes)} OCR words.")
        except Exception as e:
            print(f"Error loading OCR data from {image_path}: {e}")

    def toggle_debug(self):
        if not self.debug_shapes:
            self.load_debug_data()
            
        self.is_debug_mode = not self.is_debug_mode
        self.show_test_image = self.is_debug_mode
        self.update()

    def test_locator(self):
        print("Testing Locator -> Overlay...")
        image_path = "sample2.png"
        if len(sys.argv) > 1:
            image_path = sys.argv[1]
            
        try:
            if not self.test_image_pixmap:
                self.test_image_pixmap = QPixmap(image_path)
                
            ocr_data = extract_ocr_data(image_path)
            from src.ocr_locator import find_text
            
            target = "matrix"
            print(f"Looking for '{target}'...")
            box = find_text(ocr_data, target)
            
            if box:
                print(f"Found '{target}' at {box}")
                self.is_debug_mode = False
                self.show_test_image = True
                
                # Create attention shapes over the located box
                self.normal_shapes = [
                    RectangleShape(
                        x=box["left"], y=box["top"], 
                        width=box["width"], height=box["height"], 
                        padding=8, outline_color="red"
                    ),
                    CircleShape(
                        x=box["left"], y=box["top"], 
                        width=box["width"], height=box["height"], 
                        padding=15, outline_color="blue"
                    ),
                    LabelShape(
                        x=box["left"], y=box["top"] - 40,
                        width=150, height=30,
                        text=f"Found: {target}",
                        bg_color="yellow", text_color="black"
                    )
                ]
                self.update()
            else:
                print(f"Could not find '{target}' in the image.")
                
        except Exception as e:
            print(f"Error testing locator: {e}")

    def test_lesson(self):
        print("Testing Lesson Pipeline...")
        image_path = "sample2.png"
        if len(sys.argv) > 1:
            image_path = sys.argv[1]
            
        if not self.test_image_pixmap:
            self.test_image_pixmap = QPixmap(image_path)
            
        self.ocr_data = extract_ocr_data(image_path)
        
        mock_response = """
STEP 1
TITLE: Identify the input
ANCHOR: matrix
CONTEXT: You are given an n x n 2D matrix
ATTENTION: rectangle
EMPHASIS: high
EXPLANATION: This is the 2D array representing the image.

STEP 2
TITLE: Output requirement
ANCHOR: rotate the image
CONTEXT: rotate the image by 90 degrees
ATTENTION: underline
EMPHASIS: medium
EXPLANATION: The goal is to rotate it in place without using another matrix.
"""
        from src.lesson_engine import parse_lesson_steps
        self.lesson_steps = parse_lesson_steps(mock_response)
        self.current_step_index = 0
        self.show_current_step()

    def show_current_step(self):
        if not hasattr(self, 'lesson_steps') or not self.lesson_steps:
            return
            
        step = self.lesson_steps[self.current_step_index]
        print(f"Showing Step {step['step']}: {step['title']}")
        
        from src.ocr_locator import find_text
        box = find_text(self.ocr_data, step["anchor"], step["context"])
        
        self.is_debug_mode = False
        self.show_test_image = True
        
        if box:
            attention_type = step.get("attention", "rectangle")
            
            shape = None
            if attention_type == "circle":
                shape = CircleShape(x=box["left"], y=box["top"], width=box["width"], height=box["height"])
            elif attention_type == "underline":
                shape = UnderlineShape(x=box["left"], y=box["top"], width=box["width"], height=box["height"])
            else:
                shape = RectangleShape(x=box["left"], y=box["top"], width=box["width"], height=box["height"])
                
            self.normal_shapes = [
                shape,
                LabelShape(
                    x=box["left"], y=max(0, box["top"] - 40),
                    width=250, height=30,
                    text=f"Step {step['step']}: {step['title']}",
                    bg_color="white", text_color="black"
                )
            ]
        else:
            print(f"Could not find anchor '{step['anchor']}' for Step {step['step']}")
            self.normal_shapes = []
            
        self.update()
        
    def next_step(self):
        if hasattr(self, 'lesson_steps') and self.lesson_steps:
            if self.current_step_index < len(self.lesson_steps) - 1:
                self.current_step_index += 1
                self.show_current_step()

    def prev_step(self):
        if hasattr(self, 'lesson_steps') and self.lesson_steps:
            if self.current_step_index > 0:
                self.current_step_index -= 1
                self.show_current_step()

    def paintEvent(self, event):
        painter = QPainter(self)
        
        # Draw the test image as a background if enabled so user can verify alignment
        if self.show_test_image and self.test_image_pixmap:
            painter.drawPixmap(0, 0, self.test_image_pixmap)
            
        renderer = Renderer(painter)
        
        shapes_to_draw = self.debug_shapes if self.is_debug_mode else self.normal_shapes
        
        for shape in shapes_to_draw:
            renderer.draw(shape)

class ControlPanel(QWidget):
    def __init__(self, overlay):
        super().__init__()
        self.overlay = overlay
        self.setWindowTitle("ClickTutor Controls")
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint)
        self.resize(300, 250)
        
        layout = QVBoxLayout()
        self.btn_debug = QPushButton("Toggle OCR Debug Mode (F8)")
        self.btn_debug.clicked.connect(self.overlay.toggle_debug)
        layout.addWidget(self.btn_debug)
        
        self.btn_locate = QPushButton("Test Locator ('matrix')")
        self.btn_locate.clicked.connect(self.overlay.test_locator)
        layout.addWidget(self.btn_locate)
        
        self.btn_lesson = QPushButton("Test Lesson Pipeline (Milestone 5)")
        self.btn_lesson.clicked.connect(self.overlay.test_lesson)
        layout.addWidget(self.btn_lesson)
        
        self.btn_prev = QPushButton("< Previous Step")
        self.btn_prev.clicked.connect(self.overlay.prev_step)
        layout.addWidget(self.btn_prev)
        
        self.btn_next = QPushButton("Next Step >")
        self.btn_next.clicked.connect(self.overlay.next_step)
        layout.addWidget(self.btn_next)
        
        self.btn_quit = QPushButton("Exit")
        self.btn_quit.clicked.connect(QApplication.quit)
        layout.addWidget(self.btn_quit)
        
        self.setLayout(layout)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_F8:
            self.overlay.toggle_debug()
        else:
            super().keyPressEvent(event)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    overlay = OverlayPoC()
    overlay.show()
    
    panel = ControlPanel(overlay)
    panel.show()
    
    print("Overlay is running. Use the Control Panel window to toggle Debug Mode.")
    sys.exit(app.exec())
