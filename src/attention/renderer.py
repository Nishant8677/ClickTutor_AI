from PyQt6.QtGui import QPainter, QPen, QColor, QFont
from PyQt6.QtCore import Qt, QRect, QPoint
from src.attention.shapes import AttentionShape, RectangleShape, CircleShape, UnderlineShape, LabelShape, DebugBoxShape

class Renderer:
    def __init__(self, painter: QPainter):
        self.painter = painter
        self.painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    def draw(self, shape: AttentionShape):
        # Layer 1: Glow (underneath)
        if shape.glow_strength > 0.0:
            self._draw_glow(shape)
            
        # Layer 2: Crisp Shape
        self._draw_shape(shape)
        
        # Layer 3: Text / Labels
        if isinstance(shape, LabelShape) or isinstance(shape, DebugBoxShape):
            self._draw_text(shape)

    def _get_color_with_opacity(self, hex_color_or_name, opacity, extra_alpha_mult=1.0):
        color = QColor(hex_color_or_name)
        # alpha is 0-255
        final_alpha = int(255 * opacity * extra_alpha_mult)
        color.setAlpha(max(0, min(255, final_alpha)))
        return color

    def _draw_shape_geometry(self, shape: AttentionShape, pen: QPen, brush=None):
        self.painter.setPen(pen)
        if brush:
            self.painter.setBrush(brush)
        else:
            self.painter.setBrush(Qt.BrushStyle.NoBrush)
            
        if isinstance(shape, RectangleShape):
            rect = QRect(
                int(shape.x - shape.padding),
                int(shape.y - shape.padding),
                int(shape.width + (shape.padding * 2)),
                int(shape.height + (shape.padding * 2))
            )
            self.painter.drawRect(rect)
        elif isinstance(shape, CircleShape):
            center_x = shape.x + (shape.width // 2)
            center_y = shape.y + (shape.height // 2)
            radius_x = (shape.width // 2) + shape.padding
            radius_y = (shape.height // 2) + shape.padding
            self.painter.drawEllipse(QPoint(int(center_x), int(center_y)), int(radius_x), int(radius_y))
        elif isinstance(shape, UnderlineShape):
            y = shape.y + shape.height + shape.padding
            self.painter.drawLine(int(shape.x), int(y), int(shape.x + shape.width), int(y))
        elif isinstance(shape, LabelShape):
            rect = QRect(int(shape.x), int(shape.y), int(shape.width), int(shape.height))
            self.painter.drawRect(rect)
        elif isinstance(shape, DebugBoxShape):
            rect = QRect(int(shape.x), int(shape.y), int(shape.width), int(shape.height))
            self.painter.drawRect(rect)

    def _draw_glow(self, shape: AttentionShape):
        if isinstance(shape, (LabelShape, DebugBoxShape)):
            return # No glow for labels/debug by default
            
        # Draw a thick, semi-transparent version of the shape
        glow_alpha = 0.5 * shape.glow_strength # Max 50% opacity for glow
        color = self._get_color_with_opacity(shape.outline_color, shape.opacity, glow_alpha)
        
        # Glow pen is 3x thicker
        glow_width = int(shape.outline_width * 3 * shape.scale)
        pen = QPen(color, glow_width)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        
        self._draw_shape_geometry(shape, pen)

    def _draw_shape(self, shape: AttentionShape):
        pen = None
        brush = None
        
        if isinstance(shape, LabelShape):
            pen = QPen(Qt.PenStyle.NoPen)
            brush_color = self._get_color_with_opacity(shape.bg_color, shape.opacity)
            brush = brush_color
        elif isinstance(shape, DebugBoxShape):
            pen_color = self._get_color_with_opacity(QColor(128, 0, 128), shape.opacity)
            pen = QPen(pen_color, 1)
            brush_color = self._get_color_with_opacity(QColor(128, 0, 128), shape.opacity, 0.4) # 40% alpha base
            brush = brush_color
        else:
            color = self._get_color_with_opacity(shape.outline_color, shape.opacity)
            width = int(shape.outline_width * shape.scale)
            pen = QPen(color, width)
            
        self._draw_shape_geometry(shape, pen, brush)

    def _draw_text(self, shape: AttentionShape):
        if isinstance(shape, LabelShape):
            color = self._get_color_with_opacity(shape.text_color, shape.opacity)
            self.painter.setPen(QPen(color))
            self.painter.setFont(QFont("Arial", 16, QFont.Weight.Bold))
            rect = QRect(int(shape.x), int(shape.y), int(shape.width), int(shape.height))
            self.painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, shape.text)
            
        elif isinstance(shape, DebugBoxShape):
            color = self._get_color_with_opacity("white", shape.opacity)
            self.painter.setPen(QPen(color))
            self.painter.setFont(QFont("Arial", 8))
            self.painter.drawText(int(shape.x), int(shape.y - 2), f"{shape.text} ({shape.confidence})")

