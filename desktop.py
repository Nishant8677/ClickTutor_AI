import sys
from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter
from src.attention.shapes import RectangleShape, CircleShape, UnderlineShape, LabelShape
from src.attention.renderer import Renderer

class OverlayPoC(QWidget):
    def __init__(self):
        super().__init__()
        # Make the window frameless, always on top, and click-through
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.WindowTransparentForInput |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        # Make it fullscreen
        screen_geometry = QApplication.primaryScreen().geometry()
        self.setGeometry(screen_geometry)
        
        # Define shapes to render
        self.shapes = [
            RectangleShape(x=200, y=200, width=150, height=50),
            CircleShape(x=475, y=200, width=50, height=50),
            UnderlineShape(x=800, y=260, width=150, height=5),
            LabelShape(x=200, y=400, width=200, height=40, text="ClickTutor Label")
        ]

    def paintEvent(self, event):
        painter = QPainter(self)
        renderer = Renderer(painter)
        
        for shape in self.shapes:
            renderer.draw(shape)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    overlay = OverlayPoC()
    overlay.show()
    print("Overlay is running. Close terminal or press Ctrl+C to stop.")
    sys.exit(app.exec())
