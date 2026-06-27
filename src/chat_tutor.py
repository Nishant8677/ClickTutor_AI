from pathlib import Path
from src.tutor import explain_image
from src.ocr_locator import extract_ocr_data
from src.lesson_engine import LessonEngine

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

        # Initialize Lesson Engine
        self.lesson_engine = LessonEngine(
            self.image_path,
            self.ocr_data,
            self.mode
        )

    def ask(self, question):
        # Keep only recent conversation
        recent_history = self.history[-10:]

        answer, highlighted_image, lesson_steps = self.lesson_engine.generate_lesson(
            question,
            recent_history,
            self.explanation
        )

        self.lesson_steps = lesson_steps

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