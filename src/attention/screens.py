"""Qt screen geometry helpers.

Capture works in physical device pixels; Qt reports logical pixels. Getting
the conversion wrong means the captured area and the overlay describe
different regions, and every highlight lands somewhere the user is not
looking. This module owns that one conversion so the controller and the
verification tool cannot drift apart.
"""

from __future__ import annotations

from typing import Any


def physical_region(screen: Any) -> dict[str, int] | None:
    """Returns a screen's bounds in physical pixels, for passing to mss.

    Args:
        screen: A QScreen, or None.

    Returns:
        A ``{"left", "top", "width", "height"}`` dict in physical desktop
        pixels, or None when no screen was supplied.

    Note:
        Approximate on mixed-DPI multi-monitor setups. Qt normalises logical
        coordinates across screens, so a screen's origin is not necessarily a
        uniform multiple of its own device pixel ratio.
    """
    if screen is None:
        return None

    geometry = screen.geometry()
    ratio = screen.devicePixelRatio()
    return {
        "left": round(geometry.x() * ratio),
        "top": round(geometry.y() * ratio),
        "width": round(geometry.width() * ratio),
        "height": round(geometry.height() * ratio),
    }
