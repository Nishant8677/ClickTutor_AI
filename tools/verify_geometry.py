"""Checks that screen capture and the overlay agree about geometry.

The product promise is that a highlight lands exactly on the thing it refers
to. That only holds if the captured image and the overlay widget describe the
same region of the same screen. Capture works in physical device pixels; Qt
reports logical pixels; the two differ whenever the OS applies display
scaling.

This is the numeric half of that check and needs no human judgement: it
compares the captured image against the overlay's own geometry and reports a
pass or fail. Run it once per display-scaling setting, and once per monitor.

    python tools/verify_geometry.py

Exits 0 when consistent, 1 otherwise. Tesseract is not required.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Python puts this file's directory on sys.path, not the repository root, so
# "src" is not importable when invoked as `python tools/verify_geometry.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtWidgets import QApplication  # noqa: E402

from src.attention.coordinates import CoordinateMapper  # noqa: E402
from src.attention.overlay import TransparentOverlay  # noqa: E402
from src.attention.screens import physical_region  # noqa: E402
from src.capture import ScreenCapture  # noqa: E402
from src.console import configure_stdio  # noqa: E402

# A highlight this far out of place is still recognisably on target. Tighter
# than a character width, looser than float noise and rounding.
TOLERANCE_PX = 2


def _check(label: str, passed: bool, detail: str) -> bool:
    print(f"  [{'PASS' if passed else 'FAIL'}] {label}: {detail}")
    return passed


def main() -> int:
    configure_stdio()
    app = QApplication(sys.argv)
    overlay = TransparentOverlay()
    screen = overlay.screen_target

    geometry = screen.geometry()
    ratio = screen.devicePixelRatio()

    print("Screen")
    print(f"  name                : {screen.name()}")
    print(
        f"  logical geometry    : {geometry.width()}x{geometry.height()} "
        f"at ({geometry.x()}, {geometry.y()})"
    )
    print(f"  devicePixelRatio    : {ratio}")
    print(
        f"  implied physical    : {round(geometry.width() * ratio)}x"
        f"{round(geometry.height() * ratio)}"
    )
    print(f"  overlay widget size : {overlay.width()}x{overlay.height()}")

    region = physical_region(screen)
    print(f"  capture region      : {region}")

    print("\nCapturing...")
    try:
        image = ScreenCapture().capture(region=region)
    except Exception as exc:  # noqa: BLE001 - report any capture failure plainly
        print(f"  CAPTURE FAILED: {type(exc).__name__}: {exc}")
        print("\nRESULT: FAIL (could not capture the screen at all)")
        return 1

    print(f"  captured image      : {image.width}x{image.height}")

    overlay.set_source_size(image.width, image.height)
    mapper = overlay.mapper
    print(f"  mapper scale        : {mapper.scale:.4f}")
    print(f"  mapper offsets      : ({mapper.offset_x:.2f}, {mapper.offset_y:.2f})")

    print("\nChecks")
    results = []

    results.append(
        _check(
            "capture is non-empty",
            image.width > 0 and image.height > 0,
            f"{image.width}x{image.height}",
        )
    )

    capture_aspect = image.width / image.height
    overlay_aspect = overlay.width() / overlay.height()
    results.append(
        _check(
            "aspect ratios match",
            abs(capture_aspect - overlay_aspect) < 0.01,
            f"capture {capture_aspect:.4f} vs overlay {overlay_aspect:.4f}",
        )
    )

    # If capture really is the physical pixels of this screen, the mapper must
    # be undoing exactly the display scaling.
    expected_scale = 1 / ratio if ratio else 1.0
    results.append(
        _check(
            "mapper scale undoes display scaling",
            abs(mapper.scale - expected_scale) < 0.01,
            f"scale {mapper.scale:.4f} vs expected {expected_scale:.4f} (1/DPR)",
        )
    )

    results.append(
        _check(
            "no letterboxing (capture covers the whole overlay)",
            abs(mapper.offset_x) <= TOLERANCE_PX and abs(mapper.offset_y) <= TOLERANCE_PX,
            f"offsets ({mapper.offset_x:.2f}, {mapper.offset_y:.2f})",
        )
    )

    # The corner round-trip is the one that would have caught the original bug:
    # an unmapped box at the far edge of a scaled capture landed outside the
    # widget entirely and was never drawn.
    far = mapper.map_box(
        {
            "left": image.width - 20,
            "top": image.height - 20,
            "width": 20,
            "height": 20,
        }
    )
    inside = (
        far["left"] + far["width"] <= overlay.width() + TOLERANCE_PX
        and far["top"] + far["height"] <= overlay.height() + TOLERANCE_PX
    )
    results.append(
        _check(
            "bottom-right box maps inside the overlay",
            inside,
            f"{far} within {overlay.width()}x{overlay.height()}",
        )
    )

    corner_x, corner_y = mapper.map_point(image.width, image.height)
    results.append(
        _check(
            "capture corner maps to overlay corner",
            abs(corner_x - overlay.width()) <= TOLERANCE_PX
            and abs(corner_y - overlay.height()) <= TOLERANCE_PX,
            f"({corner_x}, {corner_y}) vs ({overlay.width()}, {overlay.height()})",
        )
    )

    identity = CoordinateMapper.identity()
    drift_x, drift_y = identity.map_point(image.width, image.height)
    print(
        f"\nWithout the transform a corner box would be drawn at "
        f"({drift_x}, {drift_y}) on a {overlay.width()}x{overlay.height()} "
        f"widget — off by {drift_x - overlay.width()}px horizontally."
    )

    ok = all(results)
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
    if ok:
        print(
            "Geometry is consistent. Confirm visually with Ctrl+Shift+D in "
            "the running app: debug boxes should sit on the real words."
        )
    else:
        print("Capture and overlay disagree. Highlights will be misplaced.")

    app.quit()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
