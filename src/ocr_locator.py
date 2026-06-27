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


def clean_context(text):
    return text.lower().strip()


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
        "height": bottom - top
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
                "line_id": (block_nums[i], par_nums[i], line_nums[i])
            }
        )

    return words


def get_line_texts(words):
    lines = {}
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


def find_text(ocr_data, target_text, context_text=None):
    print(f"Searching OCR for target: '{target_text}' (Context: '{context_text}')")

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
        
        print(f"Selected candidate based on context score: {best_score}")
        return make_box(best_cand, scale)

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
                candidates.append(words[i:i+n])
        
        if candidates:
            print("Found Exact Phrase Candidates!")
            return select_best(candidates)

    # =====================================
    # PASS 2 : Exact word
    # =====================================
    candidates = []
    for target in target_words:
        for w in words:
            if w["text"] == target:
                candidates.append([w])
    
    if candidates:
        print("Found Exact Word Candidates!")
        return select_best(candidates)

    # =====================================
    # PASS 3 : Fuzzy phrase
    # =====================================
    candidates = []
    if n > 1:
        target_phrase = "".join(target_words)
        for i in range(len(words) - n + 1):
            candidate = "".join(w["text"] for w in words[i:i+n])
            if similarity(candidate, target_phrase) >= FUZZY_MATCH_THRESHOLD:
                candidates.append(words[i:i+n])
        
        if candidates:
            print("Found Fuzzy Phrase Candidates!")
            return select_best(candidates)

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
        print("Found Fuzzy Word Candidates!")
        return select_best(candidates)

    # =====================================
    # PASS 5 : Conservative partial match
    # =====================================
    candidates = []
    for target in target_words:
        for w in words:
            word = w["text"]
            if is_useful_partial(target, word):
                candidates.append([w])
    
    if candidates:
        print("Found Partial Match Candidates!")
        return select_best(candidates)

    print("Not Found")
    return None
