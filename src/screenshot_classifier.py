import os
from PIL import Image
from src.tutor import model
from src.ocr_locator import build_words

CODE_KEYWORDS = {
    "def", "class", "import", "struct", "fn", "namespace",
    "public", "private", "return", "include", "iostream",
    "println", "console.log", "nullptr", "sizeof", "lambda"
}

def classify_heuristically(ocr_data):
    """
    Tries to classify the image as 'code' using OCR text heuristics.
    Returns 'code' if confident, otherwise None.
    """
    if not ocr_data or "text" not in ocr_data:
        return None

    # Extracted words from build_words
    words = build_words(ocr_data)
    word_set = {w["text"].lower() for w in words}

    # If any keyword is found, classify as code
    intersection = CODE_KEYWORDS.intersection(word_set)
    if intersection:
        return "code"

    return None

def classify_with_gemini(image_path):
    """
    Uses Gemini Vision to classify the image into standard categories.
    """
    prompt = """
    Classify this screenshot into exactly one of the following categories:
    - code (IDE, text editor, terminal, source code)
    - math (equations, arithmetic, geometry, graphs with formulas)
    - diagram (flowcharts, UML, network diagrams, trees, architecture diagrams)
    - dashboard (analytics, charts, tabular data, control panels)
    - slides (presentations, powerpoint, google slides)
    - pdf (textbook pages, documents, papers)
    - website (normal web browser viewing documentation, articles, or social media)
    - other (any other type of image)

    Return ONLY the category name in lowercase (e.g. "math"). Do not output any other text or explanation.
    """
    try:
        with Image.open(image_path) as image:
            response = model.generate_content([
                prompt,
                image
            ])
            classification = response.text.strip().lower()
            # Clean up response in case it returned extra text
            valid_categories = {"code", "math", "diagram", "dashboard", "slides", "pdf", "website", "other"}
            for cat in valid_categories:
                if cat in classification:
                    return cat
            return "other"
    except Exception as e:
        return "other"

def classify_screenshot(image_path, ocr_data):
    """
    Main entry point: tries heuristics first, falls back to Gemini.
    """
    heuristic_res = classify_heuristically(ocr_data)
    if heuristic_res:
        return f"{heuristic_res} (via Heuristics)"
    
    gemini_res = classify_with_gemini(image_path)
    return gemini_res
