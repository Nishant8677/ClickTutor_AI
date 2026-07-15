from src.ocr_locator import extract_ocr_data, find_text
import json

image_path = "kth_missing.png"
demo_path = "src/desktop/demos/kth_missing.json"

ocr_data = extract_ocr_data(image_path)
with open(demo_path, "r") as f:
    demo = json.load(f)

from src.lesson_engine import parse_lesson_steps
steps = parse_lesson_steps(demo["lesson_text"])

for i, step in enumerate(steps):
    anchor = step["anchor"]
    context = step["context"]
    box = find_text(ocr_data, anchor, context)
    if box:
        print(f"Step {i+1} [{anchor}]: FOUND at {box}")
    else:
        print(f"Step {i+1} [{anchor}]: NOT FOUND!")
