"""Finds every place a phrase appears in OCR output, and reads text under a box.

The shipping locator answers "where is this phrase?" with one box, which is all
a highlight needs. Measuring a second locator against it needs more than that,
because a single reference box makes three different outcomes look identical:

  * the other locator is wrong
  * the other locator found a *different, equally valid* occurrence
  * the reference box itself is wrong

All three were visible in the first run of the locator experiment. ``arr``
appears many times in a code screenshot; OCR returned one instance and the
vision model returned another, scoring zero. For the single character ``k``,
OCR returned a 199-pixel-wide box spanning most of a line, so the reference was
simply not the character.

So this module supports scoring a box on its own terms: read the words that
fall inside it, and ask whether they contain the phrase. That needs no
reference box and cannot inherit the reference's mistakes.

Pure: no network, no Qt, no Tesseract call. Operates on already-extracted data.
"""

from __future__ import annotations

from typing import Any

from src.ocr_locator import Box, OcrData, build_words, normalize, scale_box_to_image


def word_boxes(ocr_data: OcrData) -> list[dict[str, Any]]:
    """Returns every OCR word with its box converted to image pixels.

    OCR runs on an upscaled image, so raw word coordinates are in that larger
    space. Everything here works in image pixels so boxes are comparable with
    what a locator returns.
    """
    scale = ocr_data.get("_scale", 1)
    placed = []
    for word in build_words(ocr_data):
        box = scale_box_to_image(
            {
                "left": word["left"],
                "top": word["top"],
                "width": word["width"],
                "height": word["height"],
            },
            scale,
        )
        if box:
            placed.append({"text": word["text"], "line_id": word["line_id"], "box": box})
    return placed


def find_all_occurrences(ocr_data: OcrData, phrase: str) -> list[Box]:
    """Returns a box for every occurrence of a phrase, not just the first.

    Matching mirrors the locator's line-substring pass: words on a line are
    normalised and concatenated, and the phrase is sought within that string.
    A phrase may therefore span several words.

    Args:
        ocr_data: Output of :func:`src.ocr_locator.extract_ocr_data`.
        phrase: The text to find.

    Returns:
        One box per occurrence, in no particular order. Empty if the phrase
        does not appear or is empty once normalised.
    """
    target = normalize(phrase)
    if not target:
        return []

    lines: dict[tuple, list[dict[str, Any]]] = {}
    for word in word_boxes(ocr_data):
        lines.setdefault(word["line_id"], []).append(word)

    found: list[Box] = []
    for line_words in lines.values():
        ordered = sorted(line_words, key=lambda w: w["box"]["left"])
        joined = "".join(w["text"] for w in ordered)

        start = joined.find(target)
        while start != -1:
            end = start + len(target)
            cursor, matched = 0, []
            for word in ordered:
                word_start, word_end = cursor, cursor + len(word["text"])
                cursor = word_end
                # Overlap, not containment: a phrase can begin or end partway
                # through a word.
                if not (word_end <= start or word_start >= end):
                    matched.append(word)

            if matched:
                found.append(_bounding_box([w["box"] for w in matched]))
            start = joined.find(target, start + 1)

    return found


def words_inside(ocr_data: OcrData, box: Box) -> list[dict[str, Any]]:
    """Returns the OCR words whose centres fall within a box, in reading order.

    Centres rather than full containment: a highlight drawn tightly around a
    word often clips a pixel or two of it, and requiring total containment
    would discard exactly the words the box was drawn for.
    """
    left, top = float(box["left"]), float(box["top"])
    right, bottom = left + float(box["width"]), top + float(box["height"])

    inside = []
    for word in word_boxes(ocr_data):
        centre_x = word["box"]["left"] + word["box"]["width"] / 2
        centre_y = word["box"]["top"] + word["box"]["height"] / 2
        if left <= centre_x <= right and top <= centre_y <= bottom:
            inside.append(word)

    return sorted(inside, key=lambda w: (w["line_id"], w["box"]["left"]))


def text_inside(ocr_data: OcrData, box: Box) -> str:
    """Returns the normalised text of the words inside a box, concatenated."""
    return "".join(word["text"] for word in words_inside(ocr_data, box))


def _bounding_box(boxes: list[Box]) -> Box:
    """Returns the smallest box containing all of the given boxes.

    Coordinates stay integers: these are pixels, and a fractional box would
    not survive a round trip through the renderer anyway.
    """
    left = min(b["left"] for b in boxes)
    top = min(b["top"] for b in boxes)
    right = max(b["left"] + b["width"] for b in boxes)
    bottom = max(b["top"] + b["height"] for b in boxes)
    return {"left": left, "top": top, "width": right - left, "height": bottom - top}
