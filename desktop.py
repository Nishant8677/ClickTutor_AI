import sys
from PyQt6.QtWidgets import QApplication, QWidget, QPushButton, QVBoxLayout
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter
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

    def load_debug_data(self):
        print("Loading OCR data for debug mode...")
        # Get image path from command line or use a default
        image_path = "sample2.png"
        if len(sys.argv) > 1:
            image_path = sys.argv[1]
            
        print(f"Running OCR on: {image_path}")
        try:
            ocr_data = extract_ocr_data(image_path)
            words = build_words(ocr_data, min_confidence=0)
            scale = ocr_data.get("_scale", 1)
            
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
        self.update()

    def test_locator(self):
        print("Testing Locator -> Overlay...")
        image_path = "sample2.png"
        if len(sys.argv) > 1:
            image_path = sys.argv[1]
            
        try:
            ocr_data = extract_ocr_data(image_path)
            # Find a word that exists in the image. "matrix" is in the sample.
            from src.ocr_locator import find_text
            
            target = "matrix"
            print(f"Looking for '{target}'...")
            box = find_text(ocr_data, target)
            
            if box:
                print(f"Found '{target}' at {box}")
                self.is_debug_mode = False
                
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

    def paintEvent(self, event):
        painter = QPainter(self)
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
        self.resize(250, 150)
        
        layout = QVBoxLayout()
        self.btn_debug = QPushButton("Toggle OCR Debug Mode (F8)")
        self.btn_debug.clicked.connect(self.overlay.toggle_debug)
        layout.addWidget(self.btn_debug)
        
        self.btn_locate = QPushButton("Test Locator ('matrix')")
        self.btn_locate.clicked.connect(self.overlay.test_locator)
        layout.addWidget(self.btn_locate)
        
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
