import logging
import os
import re
import shutil
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import pytesseract
from PIL import Image

logger = logging.getLogger(__name__)

OCR_SCALE = 3
MIN_CONFIDENCE = 0
FUZZY_MATCH_THRESHOLD = 0.82

# Where the common Windows installers put tesseract.exe. The UB Mannheim build
# does not reliably add itself to PATH, and a process that was already running
# would not see the change anyway, so fall back to looking for it directly.
_WINDOWS_TESSERACT_CANDIDATES = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe"),
)


def find_tesseract() -> str | None:
    """Returns a usable tesseract executable path, or None if there is none.

    Prefers PATH, then falls back to the standard Windows install locations.
    """
    on_path = shutil.which("tesseract")
    if on_path:
        return on_path

    for candidate in _WINDOWS_TESSERACT_CANDIDATES:
        if candidate and Path(candidate).is_file():
            return candidate

    return None


def configure_tesseract() -> str | None:
    """Points pytesseract at the Tesseract binary if it is not already on PATH.

    Returns:
        The path in use, or None if Tesseract could not be found at all.
    """
    located = find_tesseract()
    if located:
        pytesseract.pytesseract.tesseract_cmd = located
        logger.debug("Using Tesseract at %s", located)
    return located


# Resolved once at import: the install location does not change mid-run, and
# doing it here means every caller benefits without having to remember to.
configure_tesseract()

# Anything Tesseract can be pointed at: a path, or an already-loaded image.
ImageSource = str | os.PathLike | Image.Image

# The dict pytesseract returns, plus the "_scale" key this module adds.
OcrData = dict[str, Any]

# A bounding box in image pixels.
Box = dict[str, int]

# One OCR word, as produced by build_words.
Word = dict[str, Any]


def normalize(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "", text)
    return text


def clean_context(text: str) -> str:
    return text.lower().strip()


def confidence_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return -1.0


def similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, left, right).ratio()


def is_useful_partial(left: str, right: str) -> bool:
    if len(left) < 4 or len(right) < 4:
        return False
    return left in right or right in left


def extract_ocr_data(image_or_path: ImageSource) -> OcrData:
    """Runs Tesseract over an image and returns its raw word data.

    Args:
        image_or_path: A filesystem path, or an already-loaded PIL Image.

    Returns:
        The pytesseract DICT output, with an added ``_scale`` key recording the
        upscale factor that every coordinate in it is expressed in.

    Raises:
        OSError: If a path was given and the file cannot be read.
        pytesseract.TesseractNotFoundError: If the Tesseract binary is missing.
    """
    # Annotated because Image.open returns an ImageFile, and the conversions
    # below rebind this to a plain Image. Without it every reassignment reads
    # as a type error.
    image: Image.Image

    # os.PathLike was previously not handled: a pathlib.Path fell through to
    # the else-branch and died on .copy().
    if isinstance(image_or_path, (str, os.PathLike)):
        image = Image.open(Path(image_or_path))
    elif isinstance(image_or_path, Image.Image):
        image = image_or_path.copy()
    else:
        raise TypeError(
            f"extract_ocr_data expects a path or a PIL Image, got {type(image_or_path).__name__}"
        )

    # Convert to grayscale
    image = image.convert("L")

    # Upscale to improve OCR quality. Boxes must be scaled back before drawing.
    width, height = image.size

    image = image.resize((width * OCR_SCALE, height * OCR_SCALE))

    ocr_data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)

    ocr_data["_scale"] = OCR_SCALE

    return ocr_data


def scale_box_to_image(box: Box | None, scale: int) -> Box | None:
    if box is None or scale == 1:
        return box

    left = round(box["left"] / scale)
    top = round(box["top"] / scale)
    right = round((box["left"] + box["width"]) / scale)
    bottom = round((box["top"] + box["height"]) / scale)

    return {"left": left, "top": top, "width": max(1, right - left), "height": bottom - top}


def make_box(words: list[Word], scale: int) -> Box | None:
    left = min(w["left"] for w in words)
    top = min(w["top"] for w in words)
    right = max(w["left"] + w["width"] for w in words)
    bottom = max(w["top"] + w["height"] for w in words)

    return scale_box_to_image(
        {"left": left, "top": top, "width": right - left, "height": bottom - top}, scale
    )


def build_words(ocr_data: OcrData, min_confidence: float = MIN_CONFIDENCE) -> list[Word]:
    words: list[Word] = []
    n = len(ocr_data.get("text", []))

    block_nums = ocr_data.get("block_num", [0] * n)
    par_nums = ocr_data.get("par_num", [0] * n)
    line_nums = ocr_data.get("line_num", [0] * n)
    confidences = ocr_data.get("conf", [min_confidence] * n)

    for i in range(n):
        raw_text = ocr_data["text"][i].strip()
        text = normalize(raw_text)
        confidence = confidence_value(confidences[i])

        if not text or confidence < min_confidence:
            continue

        words.append(
            {
                "text": text,
                "raw_text": raw_text,
                "confidence": confidence,
                "left": ocr_data["left"][i],
                "top": ocr_data["top"][i],
                "width": ocr_data["width"][i],
                "height": ocr_data["height"][i],
                "line_id": (block_nums[i], par_nums[i], line_nums[i]),
            }
        )

    return words


def get_line_texts(words: list[Word]) -> dict[tuple, str]:
    lines: dict[tuple, list[Word]] = {}
    for w in words:
        lid = w["line_id"]
        if lid not in lines:
            lines[lid] = []
        lines[lid].append(w)

    line_texts = {}
    for lid, line_words in lines.items():
        sorted_words = sorted(line_words, key=lambda x: x["left"])
        line_texts[lid] = " ".join(w["raw_text"] for w in sorted_words)
    return line_texts


# Names for the six passes, loosest last. Reported by find_text_detailed so a
# caller can tell an exact hit from a fuzzy one. Measured on screens OCR reads
# badly, the loose passes match garbage and return a confident box on the wrong
# thing; the strict ones do not. See benchmarks/hostile_locator.json.
PASS_LINE_SUBSTRING = "line_substring"
PASS_EXACT_PHRASE = "exact_phrase"
PASS_EXACT_WORD = "exact_word"
PASS_FUZZY_PHRASE = "fuzzy_phrase"
PASS_FUZZY_WORD = "fuzzy_word"
PASS_PARTIAL = "partial"

# Passes that matched the whole target phrase, as opposed to one word out of
# it. This is the line that predicts correctness, and it is not the line
# between exact and fuzzy matching, which is what it first looked like.
#
# Measured across both benchmark corpora (52 matches, verdicts in
# benchmarks/locator_comparison.json and benchmarks/hostile_locator.json):
#
#     phrase-level passes    40 on target, 0 wrong
#     word-level passes       0 on target, 9 wrong
#
# PASS_EXACT_WORD is the trap. It matches any single word of a multi-word
# target anywhere on screen, so "Moral: Intelligence is strength" matched one
# short word and returned an 18x7 pixel box, and "Angle: 45" and "Angle: 90"
# both matched "angle" and returned the same box. Every one was reported as a
# successful lookup.
#
# PASS_FUZZY_PHRASE is phrase-level by construction but never fired in either
# corpus, so it is unmeasured and deliberately left out of the trusted set.
# Excluding it only costs an occasional unnecessary second opinion.
TRUSTED_PASSES = frozenset({PASS_LINE_SUBSTRING, PASS_EXACT_PHRASE})

# Every pass that matches on a single word rather than the whole phrase.
WORD_LEVEL_PASSES = frozenset({PASS_EXACT_WORD, PASS_FUZZY_WORD, PASS_PARTIAL})


def find_text(
    ocr_data: OcrData,
    target_text: str,
    context_text: str | None = None,
) -> Box | None:
    """Locates text on screen using six progressively looser match passes.

    Args:
        ocr_data: Output of :func:`extract_ocr_data`.
        target_text: The ANCHOR string from a lesson step.
        context_text: The CONTEXT string, used to disambiguate when the same
            anchor appears more than once on screen.

    Returns:
        A bounding box in image pixels, or None if nothing matched. Coordinates
        are already divided back down by the OCR upscale factor.
    """
    box, _ = find_text_detailed(ocr_data, target_text, context_text)
    return box


def locate_trusted(
    ocr_data: OcrData,
    target_text: str,
    context_text: str | None = None,
) -> Box | None:
    """Locates text, returning a box only when the whole phrase matched.

    :func:`find_text` will fall back to matching a single word of a multi-word
    target, found anywhere on screen. That returns a box the caller did not ask
    for, and measured over both benchmark corpora it was wrong every time it
    happened -- 0 correct out of 9 -- while being reported as success.

    This is the same search with that outcome treated as a miss, so a caller
    that would rather draw nothing than draw the wrong thing can say so.

    Returns:
        A bounding box in image pixels, or None if the phrase was not matched
        as a phrase.
    """
    box, which = find_text_detailed(ocr_data, target_text, context_text)
    if box is None or which not in TRUSTED_PASSES:
        return None
    return box


def find_text_detailed(
    ocr_data: OcrData,
    target_text: str,
    context_text: str | None = None,
) -> tuple[Box | None, str | None]:
    """Locates text and reports which pass matched it.

    Same search as :func:`find_text`. The difference is that the caller learns
    *how* the match was made, which matters because the passes are not equally
    trustworthy: the fuzzy and partial passes will happily match against OCR
    garbage and return a box on the wrong words, with nothing to distinguish
    that from a real hit.

    Returns:
        A tuple of the box and the pass name, or ``(None, None)`` if nothing
        matched. Pass names are the ``PASS_*`` constants in this module.
    """

    if not target_text:
        return None, None

    target_words = [
        normalize(word) for word in re.split(r"[\s\-]+", target_text) if normalize(word)
    ]

    if not target_words or target_words == ["none"]:
        return None, None

    scale = ocr_data.get("_scale", 1)
    words = build_words(ocr_data)
    line_texts = get_line_texts(words)

    # Helper to pick the best candidate based on context similarity
    def select_best(candidates):
        if not candidates:
            return None
        if not context_text:
            return make_box(candidates[0], scale)

        best_cand = None
        best_score = -1
        target_context_clean = clean_context(context_text)

        for cand in candidates:
            lid = cand[0]["line_id"]
            line_text = line_texts.get(lid, "")
            score = similarity(clean_context(line_text), target_context_clean)
            if score > best_score:
                best_score = score
                best_cand = cand

        return make_box(best_cand, scale)

    # =====================================
    # PASS 0 : Line-Based Substring Phrase Matcher (handles symbols/brackets)
    # =====================================
    norm_target = normalize(target_text)
    if norm_target:
        line_groups: dict[tuple, list[Word]] = {}
        for w in words:
            lid = w["line_id"]
            if lid not in line_groups:
                line_groups[lid] = []
            line_groups[lid].append(w)

        line_candidates = []
        for line_words in line_groups.values():
            sorted_words = sorted(line_words, key=lambda x: x["left"])
            line_text_concat = "".join(w["text"] for w in sorted_words)

            if norm_target in line_text_concat:
                match_start = line_text_concat.find(norm_target)
                match_end = match_start + len(norm_target)

                char_idx = 0
                matching_words = []
                for w in sorted_words:
                    w_len = len(w["text"])
                    w_start = char_idx
                    w_end = char_idx + w_len
                    char_idx = w_end

                    if not (w_end <= match_start or w_start >= match_end):
                        matching_words.append(w)

                if matching_words:
                    line_candidates.append(matching_words)

        if line_candidates:
            return select_best(line_candidates), PASS_LINE_SUBSTRING

    # =====================================
    # PASS 1 : Exact phrase
    # =====================================
    n = len(target_words)
    candidates = []
    if n > 1:
        for i in range(len(words) - n + 1):
            match = True
            for j in range(n):
                if words[i + j]["text"] != target_words[j]:
                    match = False
                    break
            if match:
                candidates.append(words[i : i + n])

        if candidates:
            return select_best(candidates), PASS_EXACT_PHRASE

    # PASS 2 : Exact word
    candidates = []
    for target in target_words:
        for w in words:
            if w["text"] == target:
                candidates.append([w])

    if candidates:
        return select_best(candidates), PASS_EXACT_WORD

    # =====================================
    # PASS 3 : Fuzzy phrase
    # =====================================
    candidates = []
    if n > 1:
        target_phrase = "".join(target_words)
        for i in range(len(words) - n + 1):
            candidate = "".join(w["text"] for w in words[i : i + n])
            if similarity(candidate, target_phrase) >= FUZZY_MATCH_THRESHOLD:
                candidates.append(words[i : i + n])

        if candidates:
            return select_best(candidates), PASS_FUZZY_PHRASE

    # =====================================
    # PASS 4 : Fuzzy word
    # =====================================
    best_matches = []
    for target in target_words:
        if len(target) < 3:
            continue
        for w in words:
            word = w["text"]
            if len(word) < 3:
                continue
            score = similarity(word, target)
            if score >= FUZZY_MATCH_THRESHOLD:
                best_matches.append((score, [w]))

    if best_matches:
        # Sort by similarity score descending
        best_matches.sort(key=lambda x: x[0], reverse=True)
        # Select candidates that match the highest similarity score
        top_score = best_matches[0][0]
        candidates = [item[1] for item in best_matches if item[0] == top_score]
        return select_best(candidates), PASS_FUZZY_WORD

    # PASS 5 : Conservative partial match
    candidates = []
    for target in target_words:
        for w in words:
            word = w["text"]
            if is_useful_partial(target, word):
                candidates.append([w])

    if candidates:
        return select_best(candidates), PASS_PARTIAL

    return None, None
