"""OCR-backed implementation of the Locator interface.

This is a thin adapter over :mod:`src.ocr_locator`. The six-pass matcher there
is deliberately left alone -- it is well tuned and its pass ordering matters --
so this module adds only what the interface needs: a confidence score and a
threshold below which it declines to answer.
"""

from __future__ import annotations

import logging
from typing import Any

from src.locator.base import Box, Location
from src.ocr_locator import build_words, find_text

logger = logging.getLogger(__name__)

# Tesseract reports per-word confidence on a 0-100 scale.
_MAX_TESSERACT_CONFIDENCE = 100.0


def _boxes_overlap(word_box: Box, target: Box) -> bool:
    return not (
        word_box["left"] + word_box["width"] <= target["left"]
        or word_box["left"] >= target["left"] + target["width"]
        or word_box["top"] + word_box["height"] <= target["top"]
        or word_box["top"] >= target["top"] + target["height"]
    )


class OcrLocator:
    """Locates anchors with Tesseract, reporting how legible the match was.

    Args:
        min_confidence: Results at or below this 0.0-1.0 score are discarded
            and reported as "not found".

            The default is 0.0, i.e. accept anything the matcher found. That is
            intentional and not an oversight: word-level confidence filtering
            was deliberately disabled upstream (MIN_CONFIDENCE = 0) because
            dropping low-confidence words broke duplicate-anchor resolution,
            and there is no eval set yet from which to choose a safe cutoff.
            Raising this without measuring first will silently lose highlights.
    """

    source = "ocr"

    def __init__(self, min_confidence: float = 0.0) -> None:
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError(f"min_confidence must be within 0.0-1.0, got {min_confidence}")
        self.min_confidence = min_confidence

    def locate(
        self,
        ocr_data: dict[str, Any],
        anchor: str,
        context: str | None = None,
    ) -> Location | None:
        if not anchor or anchor.strip().upper() == "NONE":
            return None

        box = find_text(ocr_data, anchor, context)
        if not box:
            logger.debug("OCR locator found no match for anchor %r", anchor)
            return None

        confidence = self._confidence_for(ocr_data, box)
        if confidence < self.min_confidence:
            logger.info(
                "Discarding match for %r: confidence %.2f below threshold %.2f",
                anchor,
                confidence,
                self.min_confidence,
            )
            return None

        return Location(box=box, confidence=confidence, source=self.source)

    def _confidence_for(self, ocr_data: dict[str, Any], box: Box) -> float:
        """Mean Tesseract confidence of the words sitting inside the match.

        Coordinates in ocr_data are in upscaled OCR space while the box has
        already been scaled back down, so the words are divided before
        comparing.
        """
        scale = ocr_data.get("_scale", 1) or 1
        scores = [
            word["confidence"]
            for word in build_words(ocr_data)
            if _boxes_overlap(
                {
                    "left": word["left"] / scale,
                    "top": word["top"] / scale,
                    "width": word["width"] / scale,
                    "height": word["height"] / scale,
                },
                box,
            )
            and word["confidence"] >= 0
        ]

        if not scores:
            # A box with no legible words under it is suspicious, but the
            # matcher did find something, so report the floor rather than
            # inventing a score.
            return 0.0

        return min(1.0, (sum(scores) / len(scores)) / _MAX_TESSERACT_CONFIDENCE)
