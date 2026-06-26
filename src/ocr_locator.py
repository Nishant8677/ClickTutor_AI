import re
from PIL import Image
import pytesseract


OCR_SCALE = 3


def normalize(text):

    text = text.lower().strip()

    text = re.sub(
        r"[^a-z0-9]+",
        "",
        text
    )

    return text


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

    words = []

    for i in range(len(ocr_data["text"])):

        text = ocr_data["text"][i].strip()

        if text:

            words.append(
                {
                    "text": normalize(text),
                    "left": ocr_data["left"][i],
                    "top": ocr_data["top"][i],
                    "width": ocr_data["width"][i],
                    "height": ocr_data["height"][i]
                }
            )

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
    # PASS 3 : Longest word
    # =====================================

    if target_words:

        longest = max(
            target_words,
            key=len
        )

        for w in words:

            word = w["text"]
            if len(word) < 3:
                continue

            if longest in word or word in longest:

                print("Found Longest Word!")

                return make_box([w], scale)

    # =====================================
    # PASS 4 : Any matching word
    # =====================================

    for target in target_words:

        for w in words:

            word = w["text"]
            if len(target) < 3 or len(word) < 3:
                continue

            if target in word or word in target:

                print("Found Partial Match!")

                return make_box([w], scale)

    print("Not Found")

    return None
