"""The seam between "where is this text?" and how that question is answered.

Today the only implementation is OCR (Tesseract). The reason this interface
exists is that a future AI locator -- one that asks a vision model for pixel
coordinates directly, as clicky_repo's ElementLocationDetector does -- can be
dropped in without the controller knowing which one answered.

Kept free of Qt so it stays unit testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

# A bounding box in captured-image pixels.
Box = dict[str, int]


@dataclass(frozen=True)
class Location:
    """Where a piece of text is, and how much to trust that answer.

    Attributes:
        box: Bounding box in captured-image pixels.
        confidence: 0.0-1.0. For the OCR locator this reflects how legible
            Tesseract found the matched words; it is not a measure of whether
            the right words were matched.
        source: Which locator produced this, for logging and debugging.
    """

    box: Box
    confidence: float
    source: str


@runtime_checkable
class Locator(Protocol):
    """Anything that can find an anchor string on a captured screen."""

    def locate(
        self,
        ocr_data: dict[str, Any],
        anchor: str,
        context: str | None = None,
    ) -> Location | None:
        """Finds an anchor, or returns None when it cannot be placed.

        Returning None rather than a guessed box is deliberate: a highlight
        drawn in the wrong place is worse than no highlight at all, because it
        actively points the learner at something irrelevant.
        """
        ...
