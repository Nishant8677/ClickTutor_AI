"""Locates a phrase on screen by asking the model for pixel coordinates.

The shipping locator routes through OCR: the model names a phrase, Tesseract
finds it. That costs 4.7s of the 9.7s pipeline and constrains the model to
anchors that appear in OCR output, which is why prompts have to quote that
output back.

This module is the alternative worth measuring. The lesson request already
sends the screenshot to the model, so the image is in front of it either way --
the only difference is whether it is asked to return a string or a box.

Nothing here is wired into the shipping pipeline. It exists so
tools/locator_experiment.py can score it against OCR before anyone decides to
switch.

Coordinates follow Gemini's detection convention: [ymin, xmin, ymax, xmax],
each normalised to 0-1000 regardless of image size. The convention is stated
explicitly in the prompt rather than assumed, since a silent change to the
model's default would otherwise transpose every box.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from src.tutor import generate_content, response_text

logger = logging.getLogger(__name__)

# The coordinate space the model is asked to answer in.
_NORMALISED_MAX = 1000

# Strips ```json ... ``` fences, which the model adds even when told not to.
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class VisionLocation:
    """One localisation attempt.

    Attributes:
        box: The located box in image pixels, or None if the model reported
            the phrase absent or returned something unusable.
        prompt_tokens: Tokens billed for the request, or None if the SDK did
            not report usage.
        response_tokens: Tokens billed for the reply, or None if unreported.
        raw: The model's reply verbatim, kept so a failure can be diagnosed
            without re-running the call.
    """

    box: dict[str, int] | None
    prompt_tokens: int | None
    response_tokens: int | None
    raw: str


def build_prompt(phrase: str) -> str:
    """Builds the localisation request for one phrase."""
    return (
        "You are given a screenshot. Find the single region that best "
        f"corresponds to this phrase:\n\n{phrase}\n\n"
        "Reply with JSON and nothing else, in one of these two forms:\n"
        '{"box_2d": [ymin, xmin, ymax, xmax]}\n'
        '{"found": false}\n\n'
        f"Coordinates are integers from 0 to {_NORMALISED_MAX}, normalised to "
        "the image size, in the order ymin, xmin, ymax, xmax. The origin is "
        "the top-left corner.\n"
        'Return {"found": false} if the phrase is not visible. Do not guess a '
        "location you cannot see, and do not explain your answer."
    )


def parse_box_response(text: str, image_width: int, image_height: int) -> dict[str, int] | None:
    """Converts the model's reply into a pixel-space box.

    Args:
        text: The model's raw reply.
        image_width: Width of the image the coordinates refer to, in pixels.
        image_height: Height of that image, in pixels.

    Returns:
        A box with ``left``, ``top``, ``width`` and ``height`` keys, or None if
        the reply reported the phrase absent or could not be understood.

    Raises:
        ValueError: If either image dimension is not positive. Scaling by a
            zero dimension would collapse every box to the origin silently.
    """
    if image_width <= 0 or image_height <= 0:
        raise ValueError(f"Image dimensions must be positive; got {image_width}x{image_height}")

    stripped = _FENCE.sub("", text.strip())
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        logger.warning("Vision locator returned non-JSON: %r", text[:200])
        return None

    if not isinstance(payload, dict):
        logger.warning("Vision locator returned %s, expected an object", type(payload).__name__)
        return None

    # A model that cannot see the phrase is supposed to say so. Treat that as a
    # legitimate answer, distinct from a malformed one.
    if payload.get("found") is False:
        return None

    coords = payload.get("box_2d")
    if not isinstance(coords, list) or len(coords) != 4:
        logger.warning("Vision locator returned no usable box_2d: %r", text[:200])
        return None

    try:
        y_min, x_min, y_max, x_max = (float(value) for value in coords)
    except (TypeError, ValueError):
        logger.warning("Vision locator returned non-numeric coordinates: %r", coords)
        return None

    # Small overshoots are clamped rather than discarded: a box running one
    # unit past the edge is a rounding artefact, not a wrong answer.
    y_min, y_max = _clamp(y_min), _clamp(y_max)
    x_min, x_max = _clamp(x_min), _clamp(x_max)

    if y_max <= y_min or x_max <= x_min:
        logger.warning("Vision locator returned an inverted or empty box: %r", coords)
        return None

    left = round(x_min / _NORMALISED_MAX * image_width)
    top = round(y_min / _NORMALISED_MAX * image_height)
    right = round(x_max / _NORMALISED_MAX * image_width)
    bottom = round(y_max / _NORMALISED_MAX * image_height)

    return {
        "left": left,
        "top": top,
        # A box that rounds to zero extent would be scored as a miss even
        # though the model pointed somewhere; keep it visible at one pixel.
        "width": max(1, right - left),
        "height": max(1, bottom - top),
    }


def _clamp(value: float) -> float:
    return max(0.0, min(float(_NORMALISED_MAX), value))


def locate_phrase(image, phrase: str) -> VisionLocation:
    """Asks the model where a phrase is in an image.

    Args:
        image: A PIL Image, sent to the model as-is.
        phrase: The text to locate.

    Returns:
        A VisionLocation. ``box`` is None when the phrase was reported absent
        or the reply could not be parsed; the raw reply is retained either way.

    Raises:
        TutorConfigError: If no API key is configured.
        google.genai.errors.APIError: If every attempt failed.
        ModelResponseError: If the model returned no usable text.
    """
    response = generate_content([build_prompt(phrase), image])
    raw = response_text(response)

    usage = getattr(response, "usage_metadata", None)
    return VisionLocation(
        box=parse_box_response(raw, image.width, image.height),
        prompt_tokens=getattr(usage, "prompt_token_count", None),
        response_tokens=getattr(usage, "candidates_token_count", None),
        raw=raw,
    )
