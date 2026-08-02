import logging

from PIL import Image
from src.tutor import (
    ModelResponseError,
    TutorConfigError,
    generate_content,
    response_text,
)
from src.ocr_locator import build_words

logger = logging.getLogger(__name__)

# Compared against build_words output, whose text has already been through
# normalize(), which strips every non-alphanumeric character. Entries must
# therefore be alphanumeric too: "console.log" could never match and is
# spelled "consolelog" here for that reason.
CODE_KEYWORDS = {
    "def", "class", "import", "struct", "fn", "namespace",
    "public", "private", "return", "include", "iostream",
    "println", "consolelog", "nullptr", "sizeof", "lambda"
}

# Ordered, not a set: the containment scan below must be deterministic when a
# response mentions more than one category.
VALID_CATEGORIES = (
    "code", "math", "diagram", "dashboard", "slides", "pdf", "website", "other",
)

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
            response = generate_content([prompt, image])
            classification = response_text(response).strip().lower()
    except (TutorConfigError, ModelResponseError) as exc:
        # Falling back to "other" is fine, but it must not look the same as a
        # genuine classification: without this log an expired key, an outage
        # and an uncategorisable image were indistinguishable.
        logger.warning("Screenshot classification unavailable: %s", exc)
        return "other"
    except OSError as exc:
        logger.warning("Could not open %r for classification: %s", image_path, exc)
        return "other"
    except Exception as exc:
        logger.exception("Unexpected classification failure: %s", exc)
        return "other"

    if classification in VALID_CATEGORIES:
        return classification

    # The model occasionally wraps the answer in a sentence.
    for category in VALID_CATEGORIES:
        if category in classification:
            return category

    logger.warning("Unrecognised classification %r; treating as 'other'.", classification)
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
