"""The seam between "where is this text?" and how that question is answered.

Today the only implementation is OCR (Tesseract). The reason this interface
exists is that a future AI locator -- one that asks a vision model for pixel
coordinates directly, as clicky_repo's ElementLocationDetector does -- can be
dropped in without the controller knowing which one answered.

Kept free of Qt so it stays unit testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, TypedDict, runtime_checkable

# A bounding box in captured-image pixels.
Box = dict[str, int]

# Key under which a lesson step carries the outcome of the engine's routed
# lookup (trusted OCR, then a capped vision fallback). Three states matter:
#
#   step[STEP_LOCATION_KEY] is a StepLocation  -- a box the routing accepted
#   step[STEP_LOCATION_KEY] is None            -- the routing declined; draw nothing
#   the key is absent                          -- the step never went through the
#                                                 engine (offline demos parse
#                                                 lesson.json directly)
#
# The distinction exists because the renderer used to look every anchor up
# again with the permissive matcher, so a word-level match the engine had
# rejected came back at draw time, and a box the vision fallback had paid for
# was lost. Only the third state permits a renderer to search on its own.
STEP_LOCATION_KEY = "location"


class StepLocation(TypedDict):
    """A box the engine accepted for a step, and which locator produced it.

    Kept as a plain dict rather than a :class:`Location` because steps travel
    through Qt signals and Streamlit session state, and because the trusted
    OCR path carries no confidence score worth inventing one for.
    """

    box: Box
    source: str


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
