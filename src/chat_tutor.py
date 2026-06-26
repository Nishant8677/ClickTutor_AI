import re
from pathlib import Path


from PIL import Image
from src.tutor import explain_image, model

from src.ocr_locator import (
    extract_ocr_data,
    find_text
)

from src.highlighter import highlight_box


def get_visible_text(response):

    match = re.search(
        r"VISIBLE TEXT:\s*(.+)",
        response,
        re.IGNORECASE
    )

    if match:
        return match.group(1).strip()

    return None


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

    def ask(self, question):
        # Keep only recent conversation
        recent_history = self.history[-10:]

        history_text = ""

        for item in recent_history:
            history_text += (
                f"{item['role']}: "
                f"{item['content']}\n"
            )

        prompt = f"""

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

TASK 1:

If the student's question mentions a visible word,
phrase, variable, button, function, metric,
or label,

return THAT exact visible text.

Only return explanatory text if the thing being
asked about is not itself visible.

Examples:

Question:
What does READY mean?

VISIBLE TEXT:
READY

Question:
What does in-place mean?

VISIBLE TEXT:
in-place

Question:
What is Deviation?

VISIBLE TEXT:
Deviation

Question:
Explain this paragraph.

VISIBLE TEXT:
NONE

TASK 2:
Briefly describe the area containing that text.

TASK 3:
Explain why you selected that text.

TASK 4:
Teach the concept like an excellent personal tutor.

IMPORTANT RULES:

- Use ONLY information that is directly visible in the screenshot.
- Never invent missing text.
- Never guess hidden or cropped words.
- If text appears truncated, explicitly say it appears truncated.
- If the exact item cannot be found, say:
  "I cannot find that item in the screenshot."
- Do not substitute similar words.
- Do not infer abbreviations.
- The VISIBLE TEXT should be copied exactly as it appears in the screenshot.
- Keep VISIBLE TEXT as short as possible.
- Prefer a single word or short phrase whenever possible.
- If the student asks about an entire section or the whole image, write:
  VISIBLE TEXT: NONE

Format your response exactly like this:

VISIBLE TEXT:
...

RELEVANT AREA:
...

WHY I CHOSE IT:
...

EXPLANATION:
...

Your explanation should:
- Be clear and beginner-friendly.
- Build intuition before giving technical details.
- Stay focused only on the student's question.
- Do not explain unrelated parts of the screenshot.

"""

        try:
            with Image.open(self.image_path) as image:
                print("Before Gemini")
                response = model.generate_content([
                    prompt,
                    image
                ])
                print("After Gemini")

            answer = response.text

            visible_text = get_visible_text(answer)
            print("VISIBLE TEXT:")
            print(visible_text)

            highlighted_image = None

            if visible_text and visible_text.strip().upper() != "NONE":

                box = find_text(
                    self.ocr_data,
                    visible_text
                )

                if box:
                    print("VISIBLE TEXT:", visible_text)
                    print("BOX:", box)
                    print("Calling highlighter...")

                    image_path = Path(self.image_path)
                    highlighted_image = highlight_box(
                        self.image_path,
                        box,
                        image_path.with_name(
                            f"{image_path.stem}_highlighted.png"
                        )
                    )

        except Exception as e:

            answer = f"Error: {str(e)}"

            highlighted_image = None

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

        return answer, highlighted_image
