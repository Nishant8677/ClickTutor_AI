"""Compares two bounding boxes that claim to describe the same thing.

The anchor benchmark answers "did the lookup resolve?", which grounding makes
close to guaranteed: the model copies anchors out of OCR output, so the locator
nearly always finds them. That says nothing about whether the box is in the
right place.

Intersection-over-union does. Given a phrase that OCR located, the OCR box is a
usable reference for where that phrase actually is, so a second locator can be
scored against it without anyone hand-labelling a corpus.

The reference is only as good as the OCR behind it. On screens Tesseract reads
badly there is no ground truth here and a low score means the two locators
disagree, not that either is wrong. Those screens need a human.

Pure: no Qt, no PIL, no network.
"""

from __future__ import annotations

from collections.abc import Mapping

# A bounding box in image pixels, as produced by src.ocr_locator.find_text.
Box = Mapping[str, float]

_REQUIRED_KEYS = ("left", "top", "width", "height")


def _edges(box: Box, label: str) -> tuple[float, float, float, float]:
    """Returns (left, top, right, bottom), validating the box on the way.

    Raises:
        KeyError: If any of the four required keys is missing.
    """
    missing = [key for key in _REQUIRED_KEYS if key not in box]
    if missing:
        raise KeyError(
            f"{label} box is missing required key(s) {', '.join(missing)}; got keys {sorted(box)}"
        )

    left, top = float(box["left"]), float(box["top"])
    return left, top, left + float(box["width"]), top + float(box["height"])


def area(box: Box) -> float:
    """Returns the area of a box, treating negative extents as empty."""
    left, top, right, bottom = _edges(box, "Box")
    return max(0.0, right - left) * max(0.0, bottom - top)


def iou(predicted: Box, reference: Box) -> float:
    """Returns intersection-over-union of two boxes, in the range 0.0 to 1.0.

    Args:
        predicted: The box under test.
        reference: The box being compared against.

    Returns:
        0.0 when the boxes do not overlap, or when either has no area. A
        degenerate box scores zero rather than raising, because a locator
        returning a zero-width box is a result worth recording, not a crash.

    Raises:
        KeyError: If either box is missing a required key.
    """
    p_left, p_top, p_right, p_bottom = _edges(predicted, "Predicted")
    r_left, r_top, r_right, r_bottom = _edges(reference, "Reference")

    overlap_width = min(p_right, r_right) - max(p_left, r_left)
    overlap_height = min(p_bottom, r_bottom) - max(p_top, r_top)
    if overlap_width <= 0 or overlap_height <= 0:
        return 0.0

    intersection = overlap_width * overlap_height
    union = area(predicted) + area(reference) - intersection
    if union <= 0:
        return 0.0

    return intersection / union


def centre_distance(predicted: Box, reference: Box) -> float:
    """Returns the distance between two box centres, in pixels.

    Reported alongside IoU because the two fail differently. A locator that
    finds the right line but overshoots its width scores a poor IoU while
    pointing essentially at the right place; a locator that is confidently on
    the wrong line does not. Distance separates those cases.

    Raises:
        KeyError: If either box is missing a required key.
    """
    p_left, p_top, p_right, p_bottom = _edges(predicted, "Predicted")
    r_left, r_top, r_right, r_bottom = _edges(reference, "Reference")

    dx = ((p_left + p_right) / 2) - ((r_left + r_right) / 2)
    dy = ((p_top + p_bottom) / 2) - ((r_top + r_bottom) / 2)
    return (dx * dx + dy * dy) ** 0.5
