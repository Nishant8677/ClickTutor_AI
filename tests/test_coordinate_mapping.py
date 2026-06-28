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
        
        # The window will stay open until closed by the user.
        print("Coordinate Mapping Validation is running!")
        print("Please visually verify that the 5 colored boxes are perfectly aligned in the 4 corners and the center of your screen.")
        print("You can manually take a screenshot using Windows+Shift+S if needed.")
        print("Close the terminal or press Ctrl+C to stop.")

    def paintEvent(self, event):
        painter = QPainter(self)
        renderer = Renderer(painter)
        for shape in self.shapes:
            renderer.draw(shape)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    overlay = CoordinateCalibrationOverlay()
    overlay.show()
    sys.exit(app.exec())
