"""Regression tests for PIL-to-QPixmap conversion.

The bug: PIL.ImageQt wraps the PIL image's buffer instead of copying it, so
once the source was collected the QPixmap referenced freed memory and the
process died with an access violation inside paintEvent — far from the call
that actually caused it, and with no Python traceback.

These tests free the source image and force a collection before touching the
pixmap, which is what makes the dangling reference deterministic.
"""

import gc

import pytest
from PIL import Image
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtWidgets import QApplication

from src.attention.overlay import _pixmap_from_pil


@pytest.fixture(scope="module")
def qt_app():
    """A QApplication; harmless under the offscreen platform plugin."""
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def source_image():
    return Image.new("RGB", (64, 32), (10, 120, 230))


class TestPixmapFromPil:
    def test_preserves_dimensions(self, qt_app, source_image):
        pixmap = _pixmap_from_pil(source_image)

        assert (pixmap.width(), pixmap.height()) == (64, 32)

    def test_produces_a_usable_pixmap(self, qt_app, source_image):
        assert not _pixmap_from_pil(source_image).isNull()

    def test_survives_the_source_being_collected(self, qt_app, source_image):
        # The actual regression. Painting after the source is gone is what
        # used to segfault.
        pixmap = _pixmap_from_pil(source_image)
        del source_image
        gc.collect()

        target = QImage(64, 32, QImage.Format.Format_RGB32)
        target.fill(QColor("black"))
        painter = QPainter(target)
        painter.drawPixmap(0, 0, pixmap)
        painter.end()

        assert target.pixelColor(32, 16) == QColor(10, 120, 230)

    def test_pixel_data_is_intact_not_garbage(self, qt_app, source_image):
        # A dangling buffer can still paint *something*; checking the actual
        # colour proves the bytes are the ones we put in.
        pixmap = _pixmap_from_pil(source_image)
        del source_image
        gc.collect()

        image = pixmap.toImage()

        assert image.pixelColor(0, 0) == QColor(10, 120, 230)
        assert image.pixelColor(63, 31) == QColor(10, 120, 230)

    def test_repeated_paints_are_stable(self, qt_app, source_image):
        pixmap = _pixmap_from_pil(source_image)
        del source_image
        gc.collect()

        target = QImage(64, 32, QImage.Format.Format_RGB32)
        for _ in range(10):
            painter = QPainter(target)
            painter.drawPixmap(0, 0, pixmap)
            painter.end()

        assert target.pixelColor(10, 10) == QColor(10, 120, 230)

    def test_handles_rgba_input(self, qt_app):
        rgba = Image.new("RGBA", (8, 8), (255, 0, 0, 255))

        pixmap = _pixmap_from_pil(rgba)

        assert not pixmap.isNull()
        assert pixmap.toImage().pixelColor(4, 4) == QColor(255, 0, 0)

    def test_handles_grayscale_input(self, qt_app):
        # extract_ocr_data converts to "L"; a caller could pass one through.
        grayscale = Image.new("L", (8, 8), 128)

        pixmap = _pixmap_from_pil(grayscale)

        assert not pixmap.isNull()
        assert pixmap.toImage().pixelColor(4, 4) == QColor(128, 128, 128)

    def test_odd_width_does_not_shear_the_image(self, qt_app):
        # A wrong stride shows up as diagonal skew when width is not a
        # multiple of 4, which is why the stride is passed explicitly.
        image = Image.new("RGB", (13, 4), (0, 255, 0))

        pixmap = _pixmap_from_pil(image)
        result = pixmap.toImage()

        assert pixmap.width() == 13
        assert result.pixelColor(12, 3) == QColor(0, 255, 0)
