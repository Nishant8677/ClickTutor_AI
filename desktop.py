import sys
from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtCore import Qt, QRect, QPoint
from PyQt6.QtGui import QPainter, QPen, QColor, QFont

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

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 1. Draw a red rectangle (like a bounding box)
        pen = QPen(QColor(255, 0, 0), 4)
        painter.setPen(pen)
        painter.drawRect(QRect(200, 200, 150, 50))

        # 2. Draw a blue circle
        pen.setColor(QColor(0, 0, 255))
        painter.setPen(pen)
        painter.drawEllipse(QPoint(500, 225), 50, 50)

        # 3. Draw a green underline
        pen.setColor(QColor(0, 255, 0))
        painter.setPen(pen)
        painter.drawLine(800, 275, 950, 275)

        # 4. Draw a label (yellow background with black text)
        painter.setPen(QPen(QColor(0, 0, 0)))
        painter.setBrush(QColor(255, 255, 0))
        painter.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        text_rect = QRect(200, 400, 200, 40)
        painter.drawRect(text_rect)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, "ClickTutor Label")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    overlay = OverlayPoC()
    overlay.show()
    print("Overlay is running. Close terminal or press Ctrl+C to stop.")
    sys.exit(app.exec())
