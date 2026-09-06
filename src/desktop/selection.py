"""Drag-to-select an area of the overlay screen for the next capture.

The selector is a separate, input-taking translucent window covering the
overlay's screen. It reports a normalised, clamped rectangle in its own
logical pixels and nothing more: converting that to a physical capture region
is the controller's job, through :class:`CaptureGeometry`, so the widget never
needs to know about device pixel ratios or screen origins.

Deliberately not built here: multi-monitor selection, resizing handles, a
magnifier. Region selection is for the current overlay monitor only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QWidget

from src.attention.coordinates import CaptureGeometry

_DIM = QColor(0, 0, 0, 110)
_BORDER = QColor(138, 180, 248)
_LABEL_BG = QColor(24, 24, 30, 220)
_LABEL_FG = QColor(255, 255, 255)
_HINT = "Drag to select the area to ask about. Esc cancels."


@dataclass(frozen=True)
class ScreenSignature:
    """What identifies the screen a selection was measured against.

    A selection is only meaningful while the screen it was made on still has
    the same logical geometry and device pixel ratio. If either changes -- a
    scale change, a resolution change, the monitor being replaced -- the
    stored physical region no longer describes what the user picked.
    """

    name: str
    left: int
    top: int
    width: int
    height: int
    device_pixel_ratio: float


def screen_signature(screen: Any) -> ScreenSignature | None:
    """Snapshots a QScreen's identity, or None when there is no usable screen.

    A monitor that has been unplugged leaves the Python wrapper of its
    QScreen pointing at a deleted C++ object, and every accessor then raises
    RuntimeError. That screen is gone as far as a selection is concerned, so
    it reads as "no signature" rather than as an exception in a Qt slot.
    """
    if screen is None:
        return None
    try:
        geometry = screen.geometry()
        return ScreenSignature(
            name=screen.name(),
            left=geometry.x(),
            top=geometry.y(),
            width=geometry.width(),
            height=geometry.height(),
            device_pixel_ratio=float(screen.devicePixelRatio()),
        )
    except RuntimeError:
        return None


@dataclass(frozen=True)
class SelectedRegion:
    """A prepared area for the next capture, bound to the screen it was made on."""

    geometry: CaptureGeometry
    signature: ScreenSignature

    def describe(self) -> str:
        """Short human-readable size, in the logical pixels the user sees."""
        return f"{round(self.geometry.logical_width)} × {round(self.geometry.logical_height)}"


class SelectionStaleError(RuntimeError):
    """The screen changed after the area was selected; the region must be rebuilt."""


class RegionSelector(QWidget):
    """A full-screen translucent widget that takes one rectangular drag.

    Signals:
        selected(left, top, width, height): One valid drag, in this widget's
            logical pixels, normalised so width and height are positive and
            clamped to the widget. Emitted at most once.
        cancelled: The selection ended without a rectangle -- a zero-area
            drag, a right click, or the window being closed. Emitted at most
            once, and never after ``selected``.
        escape_pressed: Qt saw a physical Escape here. Nothing is done about
            it locally: the controller decides whether the global hook is the
            authority for that press, exactly as for the companion.
    """

    selected = pyqtSignal(int, int, int, int)
    cancelled = pyqtSignal()
    escape_pressed = pyqtSignal()

    def __init__(self, screen: Any = None) -> None:
        super().__init__()
        self._screen_target = screen or QApplication.primaryScreen()
        self._origin: QPoint | None = None
        self._current: QPoint | None = None
        self._done = False

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.CrossCursor)
        if self._screen_target is not None:
            self.setGeometry(self._screen_target.geometry())

    # ------------------------------------------------------------ lifecycle

    @property
    def is_done(self) -> bool:
        """True once a result has been reported or the selector dismissed."""
        return self._done

    def begin(self) -> None:
        """Shows the selector and asks for keyboard focus so Escape reaches it."""
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def dismiss(self) -> None:
        """Closes without reporting anything. Used when the controller cancels."""
        self._done = True
        self.close()

    def selection_rect(self) -> QRect | None:
        """The rectangle dragged so far, normalised and clamped, or None."""
        if self._origin is None or self._current is None:
            return None
        return self._normalised(self._origin, self._current)

    def _normalised(self, a: QPoint, b: QPoint) -> QRect:
        # Half-open edges: a drag from x=100 to x=300 is 200 pixels wide. The
        # clamp keeps a drag that leaves the window on the window.
        left = max(0, min(a.x(), b.x(), self.width()))
        right = max(0, min(max(a.x(), b.x()), self.width()))
        top = max(0, min(a.y(), b.y(), self.height()))
        bottom = max(0, min(max(a.y(), b.y()), self.height()))
        return QRect(left, top, right - left, bottom - top)

    def _finish(self, rect: QRect | None) -> None:
        if self._done:
            return
        self._done = True
        if rect is not None and rect.width() > 0 and rect.height() > 0:
            self.selected.emit(rect.left(), rect.top(), rect.width(), rect.height())
        else:
            self.cancelled.emit()

    # --------------------------------------------------------------- events

    def mousePressEvent(self, event) -> None:
        if self._done:
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.position().toPoint()
            self._current = self._origin
            self.update()
        elif event.button() == Qt.MouseButton.RightButton:
            self._finish(None)

    def mouseMoveEvent(self, event) -> None:
        if self._origin is None or self._done:
            return
        self._current = event.position().toPoint()
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._origin is None:
            return
        self._current = event.position().toPoint()
        self._finish(self._normalised(self._origin, self._current))

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            # A held key auto-repeats; one physical press is reported once.
            if not event.isAutoRepeat():
                self.escape_pressed.emit()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        # Closed by the window manager while a drag was possible: that is a
        # cancel, and the controller must hear about it to restore its panels.
        self._finish(None)
        super().closeEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), _DIM)

        rect = self.selection_rect()
        if rect is None or rect.isEmpty():
            self._paint_label(painter, _HINT, self.rect().center().x(), 40)
            return

        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        painter.fillRect(rect, Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setPen(QPen(_BORDER, 2))
        painter.drawRect(rect.adjusted(0, 0, -1, -1))
        self._paint_label(
            painter,
            f"{rect.width()} × {rect.height()}",
            rect.center().x(),
            max(rect.top() - 14, 14),
        )

    def _paint_label(self, painter: QPainter, text: str, center_x: int, center_y: int) -> None:
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(text) + 16
        height = metrics.height() + 8
        box = QRect(center_x - width // 2, center_y - height // 2, width, height)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_LABEL_BG)
        painter.drawRoundedRect(box, 6, 6)
        painter.setPen(_LABEL_FG)
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
