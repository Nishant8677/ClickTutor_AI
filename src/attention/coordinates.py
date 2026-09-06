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

import math
from collections.abc import Mapping
from dataclasses import dataclass

# A bounding box as produced by src.ocr_locator.find_text. Values are floats
# because callers may divide by the OCR upscale factor before mapping.
Box = Mapping[str, float]

_REGION_KEYS = ("left", "top", "width", "height")


def _require_finite(name: str, value: float) -> None:
    if not isinstance(value, int | float) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number; got {value!r}")


def _require_integral(name: str, value: float) -> int:
    """Accepts ints and whole-valued floats; rejects bools, NaN, inf and fractions."""
    _require_finite(name, value)
    if int(value) != value:
        raise ValueError(f"{name} must be a whole number of pixels; got {value!r}")
    return int(value)


@dataclass(frozen=True)
class CaptureGeometry:
    """Where a captured image sits on the desktop, and how to map it back.

    Built once from a user's selection and carried with the lesson request so
    the image, its physical location and the device pixel ratio it was
    measured under stay together. The values are a snapshot: if the screen
    signature later changes, the geometry is stale and must be rebuilt, not
    reinterpreted under a new ratio.

    Attributes:
        left: Left edge of the capture region in physical desktop pixels.
        top: Top edge of the capture region in physical desktop pixels.
        width: Width of the capture region in physical pixels.
        height: Height of the capture region in physical pixels.
        origin_dx: ``left`` minus the overlay screen's physical left edge.
        origin_dy: ``top`` minus the overlay screen's physical top edge.
        device_pixel_ratio: Physical pixels per logical pixel on that screen.
    """

    left: int
    top: int
    width: int
    height: int
    origin_dx: int
    origin_dy: int
    device_pixel_ratio: float

    def __post_init__(self) -> None:
        # Physical fields are pixel indices; a fractional or NaN value would
        # produce a region mss cannot grab and an offset that shifts highlights.
        for name in ("left", "top", "width", "height", "origin_dx", "origin_dy"):
            object.__setattr__(self, name, _require_integral(name, getattr(self, name)))
        if self.width <= 0 or self.height <= 0:
            raise ValueError(
                f"CaptureGeometry size must be positive; got {self.width}x{self.height}"
            )
        _require_finite("device_pixel_ratio", self.device_pixel_ratio)
        if self.device_pixel_ratio <= 0:
            raise ValueError(
                f"device_pixel_ratio must be positive; got {self.device_pixel_ratio!r}"
            )

    @classmethod
    def from_logical_selection(
        cls,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        screen_region: Mapping[str, int],
        device_pixel_ratio: float,
    ) -> CaptureGeometry:
        """Builds the physical capture region for a drag on the overlay.

        Args:
            x0: One corner's x, in overlay-local logical pixels.
            y0: One corner's y, in overlay-local logical pixels.
            x1: The opposite corner's x. Order relative to ``x0`` is irrelevant.
            y1: The opposite corner's y. Order relative to ``y0`` is irrelevant.
            screen_region: The overlay screen's physical bounds as a
                ``{"left", "top", "width", "height"}`` dict, e.g. from
                :func:`src.attention.screens.physical_region`.
            device_pixel_ratio: The screen's device pixel ratio.

        Returns:
            A geometry whose edges are half-open: ``[left, left + width)``.
            Leading edges are floored and trailing edges ceiled so that a
            fractional physical boundary never trims a partly covered pixel,
            and the whole rectangle is clamped to the screen.

        Raises:
            ValueError: If the ratio or any coordinate is not finite, the
                ratio is not positive, the screen region is malformed, or the
                clamped rectangle has no area.
        """
        _require_finite("device_pixel_ratio", device_pixel_ratio)
        if device_pixel_ratio <= 0:
            raise ValueError(f"device_pixel_ratio must be positive; got {device_pixel_ratio!r}")
        for name, value in (("x0", x0), ("y0", y0), ("x1", x1), ("y1", y1)):
            _require_finite(name, value)
        screen = validate_region(screen_region, what="screen_region")

        # Normalise drag direction before scaling; a right-to-left drag is a
        # rectangle like any other.
        logical_left, logical_right = sorted((x0, x1))
        logical_top, logical_bottom = sorted((y0, y1))
        # Check emptiness before rounding: equal fractional endpoints would
        # otherwise floor/ceil apart into a one-pixel crop nobody selected.
        if logical_left == logical_right or logical_top == logical_bottom:
            raise ValueError(
                "Selection has no area: logical "
                f"({logical_left}, {logical_top})-({logical_right}, {logical_bottom})"
            )

        phys_left = max(0, math.floor(logical_left * device_pixel_ratio))
        phys_top = max(0, math.floor(logical_top * device_pixel_ratio))
        phys_right = min(screen["width"], math.ceil(logical_right * device_pixel_ratio))
        phys_bottom = min(screen["height"], math.ceil(logical_bottom * device_pixel_ratio))

        width = phys_right - phys_left
        height = phys_bottom - phys_top
        if width <= 0 or height <= 0:
            raise ValueError(
                "Selection has no area on screen after clamping: logical "
                f"({logical_left}, {logical_top})-({logical_right}, {logical_bottom}) "
                f"at ratio {device_pixel_ratio} on a {screen['width']}x{screen['height']} screen"
            )

        return cls(
            left=screen["left"] + phys_left,
            top=screen["top"] + phys_top,
            width=width,
            height=height,
            origin_dx=phys_left,
            origin_dy=phys_top,
            device_pixel_ratio=float(device_pixel_ratio),
        )

    @classmethod
    def from_logical_rect(
        cls,
        left: float,
        top: float,
        width: float,
        height: float,
        screen_region: Mapping[str, int],
        device_pixel_ratio: float,
    ) -> CaptureGeometry:
        """Like :meth:`from_logical_selection`, taking a size instead of a corner."""
        for name, value in (("width", width), ("height", height)):
            _require_finite(name, value)
        return cls.from_logical_selection(
            left, top, left + width, top + height, screen_region, device_pixel_ratio
        )

    def region(self) -> dict[str, int]:
        """Returns a fresh mss-style region dict for this capture.

        A new dict every call, so a caller that mutates it cannot corrupt the
        geometry travelling with the request.
        """
        return {"left": self.left, "top": self.top, "width": self.width, "height": self.height}

    def matches_image_size(self, width: int, height: int) -> bool:
        """True if an image of this size is what the region should produce."""
        return (width, height) == (self.width, self.height)

    @property
    def logical_width(self) -> float:
        """Width of the capture region in overlay logical pixels."""
        return self.width / self.device_pixel_ratio

    @property
    def logical_height(self) -> float:
        """Height of the capture region in overlay logical pixels."""
        return self.height / self.device_pixel_ratio


def validate_region(region: object, *, what: str = "region") -> dict[str, int]:
    """Checks an mss-style region dict and returns a validated copy.

    Args:
        region: The value to check. Must be a mapping with integer ``left``,
            ``top``, ``width`` and ``height``; width and height positive.
        what: Name used in error messages.

    Returns:
        A new ``{"left", "top", "width", "height"}`` dict of ints.

    Raises:
        ValueError: If the region is not a mapping, is missing a key, or has
            a non-integer, non-finite or non-positive dimension. An empty
            dict is rejected rather than treated as "no region".
    """
    if not isinstance(region, Mapping):
        raise ValueError(f"{what} must be a mapping with keys {_REGION_KEYS}; got {region!r}")
    missing = [key for key in _REGION_KEYS if key not in region]
    if missing:
        raise ValueError(f"{what} is missing keys {missing}; got keys {sorted(region)}")

    validated: dict[str, int] = {}
    for key in _REGION_KEYS:
        validated[key] = _require_integral(f"{what}[{key!r}]", region[key])

    if validated["width"] <= 0 or validated["height"] <= 0:
        raise ValueError(
            f"{what} size must be positive; got {validated['width']}x{validated['height']}"
        )
    return validated


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
    ) -> CoordinateMapper:
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
            raise ValueError(f"CoordinateMapper dimensions must be positive; got {detail}")

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
    def crop(
        cls,
        geometry: CaptureGeometry,
        target_width: int,
        target_height: int,
    ) -> CoordinateMapper:
        """Builds a mapper that places a cropped capture where it came from.

        Unlike :meth:`fit`, the image is never scaled to the overlay. A pixel
        ``(x, y)`` of the crop lands at overlay-local logical
        ``((origin_dx + x) / ratio, (origin_dy + y) / ratio)`` and lengths
        scale by ``1 / ratio``, so highlights sit over the screen content the
        user actually selected.

        Args:
            geometry: The snapshot the image was captured under.
            target_width: Width of the overlay widget, in logical pixels.
            target_height: Height of the overlay widget, in logical pixels.

        Raises:
            ValueError: If either target dimension is not positive.
        """
        if target_width <= 0 or target_height <= 0:
            raise ValueError(
                "CoordinateMapper target dimensions must be positive; got "
                f"{target_width}x{target_height}"
            )
        ratio = geometry.device_pixel_ratio
        return cls(
            source_width=geometry.width,
            source_height=geometry.height,
            target_width=target_width,
            target_height=target_height,
            scale=1.0 / ratio,
            offset_x=geometry.origin_dx / ratio,
            offset_y=geometry.origin_dy / ratio,
        )

    @classmethod
    def identity(cls, width: int = 1, height: int = 1) -> CoordinateMapper:
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
                f"Bounding box is missing required key {exc}; got keys {sorted(box)}"
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
