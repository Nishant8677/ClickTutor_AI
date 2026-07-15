from src.ocr_locator import extract_ocr_data, build_words
ocr_data = extract_ocr_data("sample2.png")
words = build_words(ocr_data, min_confidence=0)
print(f"Loaded {len(words)} words.")


