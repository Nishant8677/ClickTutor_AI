from src.ocr_locator import extract_ocr_data, find_text

ocr_data = extract_ocr_data("selected_region.png")
result = find_text(
    ocr_data,
    "BMAT201L"
)

print(result)
