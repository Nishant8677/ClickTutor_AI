from PIL import Image
from src.tutor import explain_image, model


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
Identify the exact visible text most relevant
to the student's question.

TASK 2:
Describe where that text appears on the screen.

TASK 3:
Explain WHY you selected that area.

TASK 4:
Explain it like a tutor.

IMPORTANT:

Use ONLY information directly visible in the screenshot.

If the exact item mentioned by the user
cannot be found in the image, say:

"I cannot find that item in the screenshot."

Do NOT guess.
Do NOT substitute a similar term.
Do NOT infer missing labels.

If visible text appears truncated,
state that it appears truncated.

Do not invent the missing characters.
Do not assume abbreviations.

When describing the visual location:

- Mention approximate screen position.
- Examples:
    - top-left
    - top-center
    - top-right
    - center-left
    - center
    - center-right
    - bottom-left
    - bottom-center
    - bottom-right

- Mention color if relevant.
- Mention nearby labels if useful.

Format:

VISIBLE TEXT:
...

RELEVANT AREA:
...

VISUAL LOCATION:
...

WHY I CHOSE IT:
...

EXPLANATION:
...

Use the screenshot actively.
Do not explain unrelated parts of the image.
"""

        try:
            with Image.open(self.image_path) as image:
                response = model.generate_content([
                    prompt,
                    image
                ])

            answer = response.text

        except Exception as e:
            answer = f"Error: {str(e)}"

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

        return answer
