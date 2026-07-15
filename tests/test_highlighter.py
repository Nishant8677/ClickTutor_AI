from src.ocr_locator import extract_ocr_data, find_text
from src.highlighter import highlight_box

ocr_data = extract_ocr_data("sample2.png")
box = find_text(
    ocr_data,
    "you have to rotate the image"
)

print(box)

highlight_box(
    "sample2.png",
    box
)
