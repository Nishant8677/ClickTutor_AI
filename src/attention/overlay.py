from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QPixmap
from src.attention.renderer import Renderer
from src.attention.animation import AnimationEngine

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
        
        self.bg_pixmap = None
        self.show_bg = False
        
        # Overlay owns the animation engine and provides its own update method as callback
        self.animation_engine = AnimationEngine(self.update)

    def set_shapes(self, shapes):
        # Kick off animation sequence for the new shapes
        self.animation_engine.start(shapes)

    def set_background(self, image_path, show=True):
        if image_path:
            self.bg_pixmap = QPixmap(image_path)
        self.show_bg = show
        self.update()

    def clear(self):
        self.animation_engine.stop()
        self.animation_engine.shapes = []
        self.bg_pixmap = None
        self.show_bg = False
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        
        if self.show_bg and self.bg_pixmap:
            painter.drawPixmap(0, 0, self.bg_pixmap)
            
        renderer = Renderer(painter)
        for shape in self.animation_engine.shapes:
            renderer.draw(shape)
