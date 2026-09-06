from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QWidget

from src.attention.animation import AnimationEngine
from src.attention.coordinates import CaptureGeometry, CoordinateMapper
from src.attention.renderer import Renderer


def _pixmap_from_pil(image) -> QPixmap:
    """Converts a PIL Image to a QPixmap that owns its pixel data.

    PIL.ImageQt was used here previously. It wraps the PIL image's buffer
    rather than copying it, so once the local ImageQt object went out of scope
    the QPixmap referenced freed memory and the process died with an access
    violation on the next paintEvent — long after the call that caused it.

    Building the QImage from bytes and copying it before the source buffer can
    be collected keeps ownership unambiguous.
    """
    rgba = image.convert("RGBA")
    buffer = rgba.tobytes("raw", "RGBA")
    qimage = QImage(
        buffer,
        rgba.width,
        rgba.height,
        rgba.width * 4,  # explicit stride: QImage otherwise assumes alignment
        QImage.Format.Format_RGBA8888,
    )
    # .copy() forces a deep copy while `buffer` is still alive. Without it the
    # QPixmap would share the same soon-to-be-freed memory.
    return QPixmap.fromImage(qimage.copy())


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
        # Set only for a cropped live capture. While present the mapper places
        # the crop at its physical origin instead of fitting the whole image
        # across the widget; see set_background().
        self._capture_geometry: CaptureGeometry | None = None
        self._mapper = CoordinateMapper.identity()

        # Overlay owns the animation engine and provides its own update method as callback
        self.animation_engine = AnimationEngine(self.update)

    @property
    def mapper(self) -> CoordinateMapper:
        """Transform from captured-image pixels to this widget's coordinates."""
        return self._mapper

    @property
    def capture_geometry(self) -> CaptureGeometry | None:
        """The crop geometry the current background was captured under, if any."""
        return self._capture_geometry

    def set_source_size(self, width: int, height: int) -> None:
        """Declares the pixel size of the image that coordinates refer to.

        OCR boxes are measured against the captured image, which is in physical
        pixels; this widget is measured in logical pixels. Calling this whenever
        the source image changes keeps highlights aligned under OS display
        scaling and when a demo screenshot's resolution differs from the screen.

        Live crop geometry, if set, is kept: the controller declares the
        captured image's size after set_background and must not lose the crop
        placement by doing so.

        Raises:
            ValueError: If crop geometry is set and the size disagrees with it.
                Fitting a crop across the widget would misplace every highlight.
        """
        if self._capture_geometry is not None:
            self._check_geometry_size(self._capture_geometry, width, height)
        self._source_size = (width, height)
        self._rebuild_mapper()

    def _rebuild_mapper(self) -> None:
        if self._capture_geometry is not None:
            self._mapper = CoordinateMapper.crop(
                self._capture_geometry,
                max(1, self.width()),
                max(1, self.height()),
            )
            return
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

    def set_background(self, image_or_path, show=True, capture_geometry=None):
        """Installs the image that incoming coordinates refer to.

        Args:
            image_or_path: A PIL image, a file path, or a falsy value to keep
                the current pixmap.
            show: Whether to paint the image under the highlights.
            capture_geometry: For a live cropped capture, the
                :class:`CaptureGeometry` it was grabbed under. The mapper then
                places the crop at its physical origin. Omitted (the default)
                for full-screen and demo images, which are fitted across the
                widget as before; passing nothing also drops any earlier crop.

        Raises:
            ValueError: If ``capture_geometry`` is given and the image (or,
                with no new image, the current source size) is not the size
                that geometry describes, or the image failed to load. Nothing
                is changed when this is raised: pixmap, source size, geometry
                and mapper stay as they were, so they never disagree.
        """
        pixmap: QPixmap | None = None
        if image_or_path:
            if isinstance(image_or_path, str):
                pixmap = QPixmap(image_or_path)
            else:
                pixmap = _pixmap_from_pil(image_or_path)

        # Validate first, commit second. A geometry that survives a failed
        # load, or a pixmap installed under a geometry that contradicts it,
        # would shift every highlight by the wrong origin.
        if capture_geometry is not None:
            if pixmap is not None:
                if pixmap.isNull():
                    raise ValueError(
                        "Cannot apply capture geometry: background image failed to load"
                    )
                self._check_geometry_size(capture_geometry, pixmap.width(), pixmap.height())
            elif self._source_size is None:
                raise ValueError("Cannot apply capture geometry: no source image is set")
            else:
                self._check_geometry_size(capture_geometry, *self._source_size)

        # Always replace, never merge: stale crop metadata under a new
        # full-screen image would misplace it just as badly.
        self._capture_geometry = capture_geometry
        if pixmap is not None:
            self.bg_pixmap = pixmap
            # The background defines the coordinate space of anything drawn on
            # top of it, so adopt its size as the source.
            if not pixmap.isNull():
                self._source_size = (pixmap.width(), pixmap.height())
        self._rebuild_mapper()
        self.show_bg = show
        self.update()

    @staticmethod
    def _check_geometry_size(geometry: CaptureGeometry, width: int, height: int) -> None:
        if not geometry.matches_image_size(width, height):
            raise ValueError(
                f"Source size {width}x{height} does not match the live capture geometry "
                f"{geometry.width}x{geometry.height}; rebuild the geometry or clear it"
            )

    def clear(self):
        self.animation_engine.stop()
        self.animation_engine.shapes = []
        self.bg_pixmap = None
        self.show_bg = False
        self._capture_geometry = None
        self._rebuild_mapper()
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
