"""Reads text and its positions using NVIDIA's Nemotron OCR v2, run locally.

Florence-2 (src/florence_ocr.py) showed that a stronger reader changes which
screens count as readable -- but it runs on a third-party host, and for a tool
that reads your screen that is a privacy decision before it is an accuracy
one. Nemotron OCR v2 is the local candidate: 54M to 84M parameters, small
enough for a 4GB laptop GPU, with a word-level English model and a line-level
multilingual one.

The package compiles a CUDA extension at install time and lives in its own
virtual environment, so this module imports it lazily. Everything else --
converting the model's normalised boxes back to pixels on the original image
-- is plain Python and can be tested without the model.

This is measurement code. Nothing in the application calls it, and it should
stay that way until the numbers justify a dependency on a GPU.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image

from src.florence_ocr import TextRegion

# "sentence" is the model's line level, which is the shape Tesseract's lines
# have and what the comparison harness scores against. "word" matches
# Tesseract's word data instead.
DEFAULT_MERGE_LEVEL = "sentence"
DEFAULT_LANG = "en"

_pipelines: dict[str, Any] = {}


class NemotronError(RuntimeError):
    """Raised when the model cannot be loaded or returns nothing usable."""


def _pipeline(lang: str) -> Any:
    """Loads the model once per language and keeps it on the GPU."""
    if lang not in _pipelines:
        try:
            from nemotron_ocr.inference.pipeline_v2 import NemotronOCRV2
        except ImportError as exc:
            raise NemotronError(
                "nemotron_ocr is not installed in this environment. It lives in "
                "its own venv with a CUDA build of torch."
            ) from exc
        try:
            _pipelines[lang] = NemotronOCRV2(lang=lang)
        except Exception as exc:  # the package raises plain RuntimeError/OSError
            raise NemotronError(f"Nemotron OCR failed to load: {exc}") from exc
    return _pipelines[lang]


def regions_from_predictions(
    predictions: list[dict[str, Any]], width: int, height: int
) -> list[TextRegion]:
    """Converts the model's normalised boxes to pixel boxes on the original image.

    The model reports each box as left/right/upper/lower fractions of the
    image size. Despite the names, "upper" is the larger y value, so the
    edges are taken as min and max rather than trusted by name.
    """
    regions = []
    for entry in predictions:
        text = str(entry.get("text", "")).strip()
        if not text:
            continue
        try:
            xs = (float(entry["left"]), float(entry["right"]))
            ys = (float(entry["upper"]), float(entry["lower"]))
        except (KeyError, TypeError, ValueError):
            # One malformed region should not discard the rest of the page.
            continue
        left = round(min(xs) * width)
        top = round(min(ys) * height)
        right = round(max(xs) * width)
        bottom = round(max(ys) * height)
        regions.append(
            TextRegion(
                text=text,
                box={
                    "left": left,
                    "top": top,
                    "width": max(1, right - left),
                    "height": max(1, bottom - top),
                },
            )
        )
    return regions


def read_regions(
    image: Image.Image,
    merge_level: str = DEFAULT_MERGE_LEVEL,
    lang: str = DEFAULT_LANG,
) -> list[TextRegion]:
    """Returns every text region Nemotron OCR finds, in original image pixels.

    Args:
        image: The image to read.
        merge_level: "word", "sentence" or "paragraph" -- how the model groups
            the text it read.
        lang: "en" for the word-level English model, "multi" for the
            line-level multilingual one.

    Returns:
        One TextRegion per region, in the model's reading order. Empty if it
        found no text.

    Raises:
        NemotronError: If the package is missing, the model cannot load, or
            inference fails.
    """
    pipeline = _pipeline(lang)
    rgb = np.array(image.convert("RGB"))
    try:
        predictions = pipeline(rgb, merge_level=merge_level)
    except Exception as exc:
        raise NemotronError(f"Nemotron OCR inference failed: {exc}") from exc
    return regions_from_predictions(predictions, image.width, image.height)


def read_text(image: Image.Image, **kwargs: Any) -> str:
    """Returns everything Nemotron OCR read, one region per line."""
    return "\n".join(region.text for region in read_regions(image, **kwargs))
