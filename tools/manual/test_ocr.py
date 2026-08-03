import sys
from pathlib import Path

# Running this file directly puts tools/manual on sys.path, not the repo
# root, so "src" would not be importable without this.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.ocr_locator import build_words, extract_ocr_data

ocr_data = extract_ocr_data("sample2.png")
words = build_words(ocr_data, min_confidence=0)
print(f"Loaded {len(words)} words.")
