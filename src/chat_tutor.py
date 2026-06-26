import re
from pathlib import Path

from PIL import Image
from src.tutor import explain_image, model

from src.ocr_locator import (
    extract_ocr_data,
    find_text
)

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


class TutorSession:
    def __init__(self, image_path, mode="student"):
        self.image_path = image_path
        self.mode = mode

        # Initial explanation of the screenshot
        self.explanation = explain_image(
            image_path,
            mode=mode
        )

        # Conversation history
        self.history = []
        self.lesson_steps = []

        # Cache OCR data once
        self.ocr_data = extract_ocr_data(
            self.image_path
        )

    def get_visual_location(self, answer):

        match = re.search(
            r"VISUAL LOCATION:\s*(.*)",
            answer,
            re.IGNORECASE
        )

        if match:

            return (
                match.group(1)
                .strip()
                .lower()
            )

        return None

    def get_size(self, answer):

        match = re.search(
            r"SIZE:\s*(.*)",
            answer,
            re.IGNORECASE
        )

        if match:

            return (
                match.group(1)
                .strip()
                .lower()
            )

        return "medium"

    def build_lesson_prompt(self, question, history_text):
        return f"""

You are ClickTutor.

You are looking at:

1. A screenshot
2. Previous conversation
3. A new student question

ORIGINAL EXPLANATION:
{self.explanation}

CONVERSATION HISTORY:
{history_text}

CURRENT QUESTION:
{question}

Your job is to teach like a human tutor pointing at the screen.
Break the answer into a short guided lesson.

For each step:
- Pick one visible word, phrase, variable, button, function, line, metric, or label that best anchors that teaching step.
- Copy the visible text exactly as it appears in the screenshot.
- Keep VISIBLE TEXT short enough for OCR to locate.
- If the step is conceptual or the exact item is not visible, write VISIBLE TEXT: NONE.
- Explain only that step.
- Use only information directly visible in the screenshot.
- Never invent missing text.
- If text appears truncated, say it appears truncated in the explanation.

Return 3 to 6 steps when possible.
Use this exact format and nothing before STEP 1:

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

STEP 3
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

    def ask(self, question):
        # Keep only recent conversation
        recent_history = self.history[-10:]

        history_text = ""

        for item in recent_history:
            history_text += (
                f"{item['role']}: "
                f"{item['content']}\n"
            )

        prompt = self.build_lesson_prompt(
            question,
            history_text
        )

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
            self.lesson_steps = lesson_steps

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
            self.lesson_steps = []

        self.history.append(
            {
                "role": "user",
                "content": question
            }
        )

        self.history.append(
            {
                "role": "assistant",
                "content": answer
            }
        )

        return answer, highlighted_image, lesson_steps