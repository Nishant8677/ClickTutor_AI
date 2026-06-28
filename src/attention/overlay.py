from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QPixmap
from src.attention.renderer import Renderer

class TransparentOverlay(QWidget):
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
        
        self.shapes = []
        self.bg_pixmap = None
        self.show_bg = False

    def set_shapes(self, shapes):
        self.shapes = shapes
        self.update()

    def set_background(self, image_path, show=True):
        if image_path:
            self.bg_pixmap = QPixmap(image_path)
        self.show_bg = show
        self.update()

    def clear(self):
        self.shapes = []
        self.bg_pixmap = None
        self.show_bg = False
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        
        if self.show_bg and self.bg_pixmap:
            painter.drawPixmap(0, 0, self.bg_pixmap)
            
        renderer = Renderer(painter)
        for shape in self.shapes:
            renderer.draw(shape)
