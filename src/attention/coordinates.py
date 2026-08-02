"""Maps captured-image pixel coordinates onto overlay widget coordinates.

OCR reports bounding boxes in the pixel space of the image that was captured.
The overlay is a Qt widget measured in *logical* pixels, which differ from
physical pixels whenever the OS applies display scaling (125%, 150%, ...).
Rendering an OCR box directly as a widget coordinate is therefore only correct
on an unscaled display whose resolution happens to equal the capture's.

This module owns that conversion and nothing else. It deliberately imports
neither Qt nor PIL so it can be unit tested without a display.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

# A bounding box as produced by src.ocr_locator.find_text. Values are floats
# because callers may divide by the OCR upscale factor before mapping.
Box = Mapping[str, float]


@dataclass(frozen=True)
class CoordinateMapper:
    """An immutable affine transform from source-image space to target space.

    The transform is a uniform scale followed by a translation, so aspect ratio
    is always preserved. When the two spaces have different aspect ratios the
    source is letterboxed (centred) inside the target rather than stretched.

    Attributes:
        source_width: Width of the captured image, in pixels.
        source_height: Height of the captured image, in pixels.
        target_width: Width of the overlay widget, in logical pixels.
        target_height: Height of the overlay widget, in logical pixels.
        scale: Uniform scale factor applied to source coordinates.
        offset_x: Horizontal letterbox offset applied after scaling.
        offset_y: Vertical letterbox offset applied after scaling.
    """

    source_width: int
    source_height: int
    target_width: int
    target_height: int
    scale: float
    offset_x: float
    offset_y: float

    @classmethod
    def fit(
        cls,
        source_width: int,
        source_height: int,
        target_width: int,
        target_height: int,
    ) -> "CoordinateMapper":
        """Builds a mapper that fits the source inside the target, centred.

        Args:
            source_width: Width of the captured image, in pixels.
            source_height: Height of the captured image, in pixels.
            target_width: Width of the overlay widget, in logical pixels.
            target_height: Height of the overlay widget, in logical pixels.

        Returns:
            A mapper whose scale is ``min(target/source)`` on both axes.

        Raises:
            ValueError: If any dimension is not a positive integer. A zero
                dimension would otherwise produce a silent divide-by-zero and
                place every highlight at the origin.
        """
        dimensions = {
            "source_width": source_width,
            "source_height": source_height,
            "target_width": target_width,
            "target_height": target_height,
        }
        invalid = [name for name, value in dimensions.items() if value <= 0]
        if invalid:
            detail = ", ".join(f"{name}={dimensions[name]}" for name in invalid)
            raise ValueError(
                f"CoordinateMapper dimensions must be positive; got {detail}"
            )

        scale = min(target_width / source_width, target_height / source_height)
        offset_x = (target_width - source_width * scale) / 2
        offset_y = (target_height - source_height * scale) / 2
        return cls(
            source_width=source_width,
            source_height=source_height,
            target_width=target_width,
            target_height=target_height,
            scale=scale,
            offset_x=offset_x,
            offset_y=offset_y,
        )

    @classmethod
    def identity(cls, width: int = 1, height: int = 1) -> "CoordinateMapper":
        """Builds a 1:1 mapper, used before any source image is known."""
        return cls(
            source_width=width,
            source_height=height,
            target_width=width,
            target_height=height,
            scale=1.0,
            offset_x=0.0,
            offset_y=0.0,
        )

    def map_point(self, x: float, y: float) -> tuple[int, int]:
        """Maps a single source point to target coordinates."""
        return (
            round(x * self.scale + self.offset_x),
            round(y * self.scale + self.offset_y),
        )

    def map_length(self, length: float) -> int:
        """Maps a source distance to a target distance.

        Returns at least 1 so that a thin but real box never scales away to
        nothing and silently stops being drawn.
        """
        return max(1, round(length * self.scale))

    def map_box(self, box: Box) -> dict[str, int]:
        """Maps an OCR bounding box into target coordinates.

        Args:
            box: A mapping with ``left``, ``top``, ``width`` and ``height``
                keys, as returned by :func:`src.ocr_locator.find_text`.

        Returns:
            A new box dict in target coordinates. The input is not mutated.

        Raises:
            KeyError: If any of the four required keys is missing.
        """
        try:
            left, top = box["left"], box["top"]
            width, height = box["width"], box["height"]
        except KeyError as exc:
            raise KeyError(
                f"Bounding box is missing required key {exc}; "
                f"got keys {sorted(box)}"
            ) from exc

        mapped_left, mapped_top = self.map_point(left, top)
        return {
            "left": mapped_left,
            "top": mapped_top,
            "width": self.map_length(width),
            "height": self.map_length(height),
        }

    def source_rect_in_target(self) -> tuple[int, int, int, int]:
        """Returns the target rect the whole source image occupies.

        Used to draw the background pixmap under exactly the same transform as
        the shapes, so debug boxes stay aligned with the image behind them.

        Returns:
            A tuple of ``(left, top, width, height)``.
        """
        return (
            round(self.offset_x),
            round(self.offset_y),
            self.map_length(self.source_width),
            self.map_length(self.source_height),
        )
