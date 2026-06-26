import re
from difflib import SequenceMatcher
from PIL import Image
import pytesseract


OCR_SCALE = 3
MIN_CONFIDENCE = 35
FUZZY_MATCH_THRESHOLD = 0.82


def normalize(text):

    text = text.lower().strip()

    text = re.sub(
        r"[^a-z0-9]+",
        "",
        text
    )

    return text


def confidence_value(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return -1.0


def similarity(left, right):
    return SequenceMatcher(None, left, right).ratio()


def is_useful_partial(left, right):
    if len(left) < 4 or len(right) < 4:
        return False

    return left in right or right in left


def extract_ocr_data(image_path):
    print("Running OCR...")
    image = Image.open(image_path)

    # Convert to grayscale
    image = image.convert("L")

    # Upscale to improve OCR quality. Boxes must be scaled back before drawing.
    width, height = image.size

    image = image.resize(
        (width * OCR_SCALE, height * OCR_SCALE)
    )

    ocr_data = pytesseract.image_to_data(
        image,
        output_type=pytesseract.Output.DICT
    )

    ocr_data["_scale"] = OCR_SCALE

    return ocr_data


def scale_box_to_image(box, scale):
    if box is None or scale == 1:
        return box

    left = round(box["left"] / scale)
    top = round(box["top"] / scale)
    right = round((box["left"] + box["width"]) / scale)
    bottom = round((box["top"] + box["height"]) / scale)

    return {
        "left": left,
        "top": top,
        "width": max(1, right - left),
        "height": max(1, bottom - top)
    }


def make_box(words, scale):
    left = min(w["left"] for w in words)
    top = min(w["top"] for w in words)
    right = max(w["left"] + w["width"] for w in words)
    bottom = max(w["top"] + w["height"] for w in words)

    return scale_box_to_image(
        {
            "left": left,
            "top": top,
            "width": right - left,
            "height": bottom - top
        },
        scale
    )


def build_words(ocr_data, min_confidence=MIN_CONFIDENCE):
    words = []

    for i in range(len(ocr_data["text"])):

        raw_text = ocr_data["text"][i].strip()
        text = normalize(raw_text)
        confidences = ocr_data.get("conf", [])
        confidence = confidence_value(
            confidences[i]
            if i < len(confidences)
            else min_confidence
        )

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
                "height": ocr_data["height"][i]
            }
        )

    return words


def find_text(ocr_data, target_text):

    print("Searching OCR...")

    if not target_text:
        return None

    target_words = [

        normalize(word)

        for word in re.split(r"[\s\-]+", target_text)

        if normalize(word)

    ]

    if not target_words or target_words == ["none"]:
        return None

    scale = ocr_data.get("_scale", 1)
    words = build_words(ocr_data)

    print("OCR WORDS:")
    print([w["text"] for w in words])

    # =====================================
    # PASS 1 : Exact phrase
    # =====================================

    n = len(target_words)

    if n > 1:

        for i in range(len(words) - n + 1):

            match = True

            for j in range(n):

                if words[i + j]["text"] != target_words[j]:

                    match = False
                    break

            if match:

                print("Found Exact Phrase!")

                return make_box(words[i:i+n], scale)

    # =====================================
    # PASS 2 : Exact word
    # =====================================

    for target in target_words:

        for w in words:

            if w["text"] == target:

                print("Found Exact Word!")

                return make_box([w], scale)

    # =====================================
    # PASS 3 : Fuzzy phrase
    # =====================================

    if n > 1:

        target_phrase = "".join(target_words)

        for i in range(len(words) - n + 1):

            candidate = "".join(w["text"] for w in words[i:i+n])

            if similarity(candidate, target_phrase) >= FUZZY_MATCH_THRESHOLD:

                print("Found Fuzzy Phrase!")

                return make_box(words[i:i+n], scale)

    # =====================================
    # PASS 4 : Fuzzy word
    # =====================================

    best_match = None
    best_score = 0

    for target in target_words:

        if len(target) < 3:
            continue

        for w in words:

            word = w["text"]

            if len(word) < 3:
                continue

            score = similarity(word, target)

            if score > best_score:
                best_score = score
                best_match = w

    if best_match and best_score >= FUZZY_MATCH_THRESHOLD:

        print("Found Fuzzy Word!")

        return make_box([best_match], scale)

    # =====================================
    # PASS 5 : Conservative partial match
    # =====================================

    for target in target_words:

        for w in words:

            word = w["text"]

            if is_useful_partial(target, word):

                print("Found Partial Match!")

                return make_box([w], scale)

    print("Not Found")

    return None
