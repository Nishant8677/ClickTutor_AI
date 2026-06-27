import re
from pathlib import Path
from PIL import Image
from src.tutor import model
from src.ocr_locator import find_text
from src.highlighter import highlight_box

STEP_PATTERN = re.compile(
    r"STEP\s+(\d+)\s*(.*?)(?=\n\s*STEP\s+\d+\s*|\Z)",
    re.IGNORECASE | re.DOTALL
)

def get_visible_text(response):
    match = re.search(
        r"VISIBLE TEXT:\s*(.+)",
        response,
        re.IGNORECASE
    )
    if match:
        return match.group(1).strip()
    return None

def extract_section(text, label, next_labels=None):
    labels = next_labels or []
    next_pattern = "|".join(re.escape(item) for item in labels)

    if next_pattern:
        pattern = rf"{re.escape(label)}:\s*(.*?)(?=\n\s*(?:{next_pattern})\s*:|\Z)"
    else:
        pattern = rf"{re.escape(label)}:\s*(.*)"

    match = re.search(
        pattern,
        text,
        re.IGNORECASE | re.DOTALL
    )

    if match:
        return match.group(1).strip()

    return ""

def parse_lesson_steps(response):
    steps = []

    for match in STEP_PATTERN.finditer(response):
        step_number = int(match.group(1))
        block = match.group(2).strip()

        visible_text = extract_section(
            block,
            "VISIBLE TEXT",
            ["EXPLANATION"]
        )

        explanation = extract_section(
            block,
            "EXPLANATION"
        )

        if not explanation:
            continue

        steps.append(
            {
                "step": step_number,
                "visible_text": visible_text or "NONE",
                "explanation": explanation,
                "highlighted_image": None
            }
        )

    return steps

def format_lesson_answer(steps):
    if not steps:
        return ""

    parts = []

    for step in steps:
        parts.append(
            f"STEP {step['step']}\n\n"
            f"VISIBLE TEXT:\n{step['visible_text']}\n\n"
            f"EXPLANATION:\n{step['explanation']}"
        )

    return "\n\n".join(parts)

class LessonEngine:
    def __init__(self, image_path, ocr_data, mode="student"):
        self.image_path = image_path
        self.ocr_data = ocr_data
        self.mode = mode

    def build_lesson_prompt(self, question, history_text, explanation_text):
        return f"""
You are ClickTutor, an AI visual tutor that teaches by guiding the student's attention step-by-step.
Your goal is to explain concepts, not just point out facts or lines. Teach WHY things are there and what they mean, rather than simply listing syntax.

You are looking at:
1. A screenshot from the student's screen.
2. Previous conversation history (if any).
3. A new student question.

ORIGINAL EXPLANATION:
{explanation_text}

CONVERSATION HISTORY:
{history_text}

CURRENT QUESTION:
{question}

Your job is to break down the answer into a short, structured lesson (3 to 6 steps).
For each step, you must focus on one specific concept and anchor it to a visible element on the screen.

CRITICAL INSTRUCTIONS FOR EXPLANATION:
- Do NOT just say "Line 5 defines X". Instead explain: WHY does X exist? What problem does X solve?
- Address consequence: What would happen or break if we removed or changed this anchored element?
- Detail common mistakes or pitfalls students make regarding this concept.
- Keep explanations clear, engaging, and friendly.

FORMAT REQUIREMENT:
For each step, pick one visible word, phrase, variable, button, function, line, metric, or label that best anchors that teaching step.
Copy the visible text EXACTLY as it appears in the screenshot. It must be short enough for OCR to locate. If no specific element is visible, write VISIBLE TEXT: NONE.

Return the steps in this exact format with nothing before STEP 1:

STEP 1
VISIBLE TEXT:
...
EXPLANATION:
...

STEP 2
VISIBLE TEXT:
...
EXPLANATION:
...
"""

    def build_step_highlights(self, steps):
        image_path = Path(self.image_path)
        highlighted_steps = []

        for index, step in enumerate(steps, start=1):
            visible_text = step.get("visible_text", "")
            highlighted_image = None

            if visible_text and visible_text.strip().upper() != "NONE":
                box = find_text(
                    self.ocr_data,
                    visible_text
                )

                if box:
                    highlighted_image = highlight_box(
                        self.image_path,
                        box,
                        image_path.with_name(
                            f"{image_path.stem}_step_{index}_highlighted.png"
                        )
                    )

            highlighted_step = dict(step)
            highlighted_step["highlighted_image"] = highlighted_image
            highlighted_steps.append(highlighted_step)

        print("PARSED LESSON STEPS:")
        print(highlighted_steps)

        return highlighted_steps

    def generate_lesson(self, question, history, explanation_text):
        history_text = ""
        for item in history[-10:]:
            history_text += f"{item['role']}: {item['content']}\n"

        prompt = self.build_lesson_prompt(question, history_text, explanation_text)

        highlighted_image = None
        lesson_steps = []

        try:
            with Image.open(self.image_path) as image:
                print("Before Gemini")
                response = model.generate_content([
                    prompt,
                    image
                ])
                print("After Gemini")

            answer = response.text
            parsed_steps = parse_lesson_steps(answer)
            lesson_steps = self.build_step_highlights(parsed_steps)

            if lesson_steps:
                highlighted_image = lesson_steps[0].get("highlighted_image")
                answer = format_lesson_answer(lesson_steps)
            else:
                visible_text = get_visible_text(answer)
                print("VISIBLE TEXT:")
                print(visible_text)

                if visible_text and visible_text.strip().upper() != "NONE":
                    box = find_text(
                        self.ocr_data,
                        visible_text
                    )

                    if box:
                        highlighted_image = highlight_box(
                            self.image_path,
                            box,
                            Path(self.image_path).with_name(
                                f"{Path(self.image_path).stem}_highlighted.png"
                            )
                        )

        except Exception as e:
            answer = f"Error: {str(e)}"
            highlighted_image = None
            lesson_steps = []

        return answer, highlighted_image, lesson_steps
