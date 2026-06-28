import sys
import time
from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPainter
import os

# Add root directory to python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.attention.shapes import RectangleShape
from src.attention.renderer import Renderer

class CoordinateCalibrationOverlay(QWidget):
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
        
        w = screen_geometry.width()
        h = screen_geometry.height()
        
        box_size = 100
        padding = 0
        
        # We draw 5 boxes. To ensure we check the coordinates properly, 
        # we will use zero padding and draw exactly on these coordinates.
        self.shapes = [
            RectangleShape(x=0, y=0, width=box_size, height=box_size, padding=padding, outline_color="red"), # Top-Left
            RectangleShape(x=w-box_size, y=0, width=box_size, height=box_size, padding=padding, outline_color="green"), # Top-Right
            RectangleShape(x=(w-box_size)//2, y=(h-box_size)//2, width=box_size, height=box_size, padding=padding, outline_color="blue"), # Center
            RectangleShape(x=0, y=h-box_size, width=box_size, height=box_size, padding=padding, outline_color="yellow"), # Bottom-Left
            RectangleShape(x=w-box_size, y=h-box_size, width=box_size, height=box_size, padding=padding, outline_color="magenta") # Bottom-Right
        ]
        
        # Setup timer to capture screen after UI renders
        QTimer.singleShot(2000, self.capture_and_verify)

    def paintEvent(self, event):
        painter = QPainter(self)
        renderer = Renderer(painter)
        for shape in self.shapes:
            renderer.draw(shape)
            
    def capture_and_verify(self):
        print("Capturing screen for verification...")
        screen = QApplication.primaryScreen()
        pixmap = screen.grabWindow(0)
        img = pixmap.toImage()
        
        w = img.width()
        h = img.height()
        
        print(f"Screen size reported by Qt: {w}x{h}")
        pixmap.save("coordinate_calibration_test.png")
        print("Saved screen capture to 'coordinate_calibration_test.png'.")
        print("Please open this image and verify the 5 colored boxes are perfectly aligned in the corners and center!")
        QApplication.quit()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    overlay = CoordinateCalibrationOverlay()
    overlay.show()
    sys.exit(app.exec())
