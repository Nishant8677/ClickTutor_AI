import sys
from pathlib import Path

# Running this file directly puts tools/manual on sys.path, not the repo
# root, so "src" would not be importable without this.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.highlighter import highlight_box
from src.ocr_locator import extract_ocr_data, find_text

ocr_data = extract_ocr_data("sample2.png")
box = find_text(ocr_data, "you have to rotate the image")

print(box)

highlight_box("sample2.png", box)
