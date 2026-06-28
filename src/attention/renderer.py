from PyQt6.QtGui import QPainter, QPen, QColor, QFont
from PyQt6.QtCore import Qt, QRect, QPoint
from src.attention.shapes import AttentionShape, RectangleShape, CircleShape, UnderlineShape, LabelShape, DebugBoxShape

class Renderer:
    def __init__(self, painter: QPainter):
        self.painter = painter
        self.painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    def draw(self, shape: AttentionShape):
        if isinstance(shape, RectangleShape):
            self._draw_rectangle(shape)
        elif isinstance(shape, CircleShape):
            self._draw_circle(shape)
        elif isinstance(shape, UnderlineShape):
            self._draw_underline(shape)
        elif isinstance(shape, LabelShape):
            self._draw_label(shape)
        elif isinstance(shape, DebugBoxShape):
            self._draw_debug_box(shape)

    def _draw_rectangle(self, shape: RectangleShape):
        pen = QPen(QColor(shape.outline_color), shape.outline_width)
        self.painter.setPen(pen)
        
        rect = QRect(
            shape.x - shape.padding,
            shape.y - shape.padding,
            shape.width + (shape.padding * 2),
            shape.height + (shape.padding * 2)
        )
        self.painter.drawRect(rect)

    def _draw_circle(self, shape: CircleShape):
        pen = QPen(QColor(shape.outline_color), shape.outline_width)
        self.painter.setPen(pen)
        
        # Draw ellipse around the bounding box center
        center_x = shape.x + (shape.width // 2)
        center_y = shape.y + (shape.height // 2)
        
        radius_x = (shape.width // 2) + shape.padding
        radius_y = (shape.height // 2) + shape.padding
        
        self.painter.drawEllipse(QPoint(center_x, center_y), radius_x, radius_y)

    def _draw_underline(self, shape: UnderlineShape):
        pen = QPen(QColor(shape.outline_color), shape.outline_width)
        self.painter.setPen(pen)
        
        y = shape.y + shape.height + shape.padding
        self.painter.drawLine(shape.x, y, shape.x + shape.width, y)

    def _draw_label(self, shape: LabelShape):
        self.painter.setPen(QPen(QColor(shape.text_color)))
        self.painter.setBrush(QColor(shape.bg_color))
        self.painter.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        
        rect = QRect(shape.x, shape.y, shape.width, shape.height)
        self.painter.drawRect(rect)
        self.painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, shape.text)

    def _draw_debug_box(self, shape: DebugBoxShape):
        # Draw a semi-transparent purple box around the word
        color = QColor(128, 0, 128, 100)
        self.painter.setBrush(color)
        self.painter.setPen(QPen(QColor(128, 0, 128), 1))
        
        rect = QRect(shape.x, shape.y, shape.width, shape.height)
        self.painter.drawRect(rect)
        
        # Draw the confidence and text very small above the box
        self.painter.setPen(QPen(QColor(255, 255, 255)))
        self.painter.setFont(QFont("Arial", 8))
        self.painter.drawText(shape.x, shape.y - 2, f"{shape.text} ({shape.confidence})")

