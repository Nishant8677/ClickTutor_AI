from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QWidget

from src.attention.animation import AnimationEngine
from src.attention.coordinates import CoordinateMapper
from src.attention.renderer import Renderer


class TransparentOverlay(QWidget):
    def __init__(self, screen=None):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.screen_target = screen or QApplication.primaryScreen()
        self.setGeometry(self.screen_target.geometry())

        self.bg_pixmap = None
        self.show_bg = False

        # Size of the image that incoming coordinates were measured against.
        # Until a source is declared the overlay maps 1:1, which is only
        # correct on an unscaled display — see set_source_size().
        self._source_size = None
        self._mapper = CoordinateMapper.identity()

        # Overlay owns the animation engine and provides its own update method as callback
        self.animation_engine = AnimationEngine(self.update)

    @property
    def mapper(self) -> CoordinateMapper:
        """Transform from captured-image pixels to this widget's coordinates."""
        return self._mapper

    def set_source_size(self, width: int, height: int) -> None:
        """Declares the pixel size of the image that coordinates refer to.

        OCR boxes are measured against the captured image, which is in physical
        pixels; this widget is measured in logical pixels. Calling this whenever
        the source image changes keeps highlights aligned under OS display
        scaling and when a demo screenshot's resolution differs from the screen.
        """
        self._source_size = (width, height)
        self._rebuild_mapper()

    def _rebuild_mapper(self) -> None:
        if not self._source_size:
            self._mapper = CoordinateMapper.identity()
            return
        source_width, source_height = self._source_size
        self._mapper = CoordinateMapper.fit(
            source_width,
            source_height,
            max(1, self.width()),
            max(1, self.height()),
        )

    def resizeEvent(self, event):
        # Geometry can change when the screen resolution or scale factor does.
        self._rebuild_mapper()
        super().resizeEvent(event)

    def set_shapes(self, shapes):
        # Kick off animation sequence for the new shapes
        self.animation_engine.start(shapes)

    def set_background(self, image_or_path, show=True):
        if image_or_path:
            if isinstance(image_or_path, str):
                self.bg_pixmap = QPixmap(image_or_path)
            else:
                from PIL.ImageQt import ImageQt

                qimage = ImageQt(image_or_path)
                self.bg_pixmap = QPixmap.fromImage(qimage)
            # The background defines the coordinate space of anything drawn on
            # top of it, so adopt its size as the source.
            if not self.bg_pixmap.isNull():
                self.set_source_size(self.bg_pixmap.width(), self.bg_pixmap.height())
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
            # Draw through the same transform the shapes use, so debug boxes
            # stay registered against the image underneath them.
            left, top, width, height = self._mapper.source_rect_in_target()
            painter.drawPixmap(left, top, width, height, self.bg_pixmap)

        renderer = Renderer(painter)
        for shape in self.animation_engine.shapes:
            renderer.draw(shape)
