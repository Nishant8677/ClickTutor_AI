"""Reads text and its positions using Florence-2, hosted on fal.

Tesseract is the shipping OCR engine and it is good on screens. On handwriting,
whiteboards and photographed pages it returns text that is not language --
measured at 22 to 44 mean confidence on this project's hostile corpus, with
output like "conn OF Gene exPve SS10N". Every anchor grounded on that is
grounded on noise.

Florence-2 exposes an OCR mode that returns a box per text region, which is the
same shape Tesseract's data has, so it can be compared directly rather than
bolted on as a separate path.

This is measurement code. Nothing in the application calls it, and it should
stay that way until the numbers justify a dependency on a third-party host --
which for a tool that reads your screen is a privacy decision, not just an
accuracy one.

Requires FAL_KEY in the environment.
"""

from __future__ import annotations

import base64
import io
import os
from dataclasses import dataclass

import httpx
from PIL import Image

ENDPOINT = "https://fal.run/fal-ai/florence-2-large/ocr-with-region"

# fal accepts a data URI, which avoids uploading to their storage as a separate
# step. Large screenshots have to be scaled down to keep the request sane: a
# 3MB PNG base64s to roughly 4MB of JSON.
MAX_EDGE = 1600
JPEG_QUALITY = 90
REQUEST_TIMEOUT_SECONDS = 180

# Florence emits its end-of-sequence marker inside the first label.
_EOS = "</s>"


class FlorenceError(RuntimeError):
    """Raised when the OCR service cannot be reached or returns nothing usable."""


@dataclass(frozen=True)
class TextRegion:
    """One region of text and where it is, in original image pixels."""

    text: str
    box: dict[str, int]


def _encode(image: Image.Image) -> tuple[str, float]:
    """Returns a data URI and the factor the image was scaled by."""
    rgb = image.convert("RGB")
    scale = min(1.0, MAX_EDGE / max(rgb.width, rgb.height))
    if scale < 1.0:
        rgb = rgb.resize((round(rgb.width * scale), round(rgb.height * scale)))

    buffer = io.BytesIO()
    rgb.save(buffer, format="JPEG", quality=JPEG_QUALITY)
    encoded = base64.b64encode(buffer.getvalue()).decode()
    return f"data:image/jpeg;base64,{encoded}", scale


def read_regions(image: Image.Image, api_key: str | None = None) -> list[TextRegion]:
    """Returns every text region Florence-2 finds, in original image pixels.

    Args:
        image: The image to read.
        api_key: fal key. Defaults to the FAL_KEY environment variable.

    Returns:
        One TextRegion per detected region, in the order the service returned
        them. Empty if it found no text.

    Raises:
        FlorenceError: If FAL_KEY is missing, the request fails, or the
            response does not have the documented shape.
    """
    key = api_key or os.getenv("FAL_KEY")
    if not key:
        raise FlorenceError("FAL_KEY is not set. Add it to .env, which is gitignored.")

    data_uri, scale = _encode(image)
    try:
        response = httpx.post(
            ENDPOINT,
            headers={"Authorization": f"Key {key}", "Content-Type": "application/json"},
            json={"image_url": data_uri},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as exc:
        raise FlorenceError(f"Florence-2 request failed: {exc}") from exc

    boxes = (payload.get("results") or {}).get("quad_boxes")
    if boxes is None:
        raise FlorenceError(f"Unexpected response shape: {str(payload)[:200]}")

    regions = []
    for entry in boxes:
        text = str(entry.get("label", "")).replace(_EOS, "").strip()
        if not text:
            continue
        try:
            # Coordinates come back in the scaled image's space; undo that so
            # callers can compare with Tesseract boxes on the original.
            regions.append(
                TextRegion(
                    text=text,
                    box={
                        "left": round(float(entry["x"]) / scale),
                        "top": round(float(entry["y"]) / scale),
                        "width": max(1, round(float(entry["w"]) / scale)),
                        "height": max(1, round(float(entry["h"]) / scale)),
                    },
                )
            )
        except (KeyError, TypeError, ValueError):
            # One malformed region should not discard the rest of the page.
            continue

    return regions


def read_text(image: Image.Image, api_key: str | None = None) -> str:
    """Returns everything Florence-2 read, one region per line."""
    return "\n".join(region.text for region in read_regions(image, api_key))
