from src.tutor import explain_image, model


class TutorSession:

    def __init__(self, image_path, mode="student"):

        self.image_path = image_path
        self.mode = mode

        self.explanation = explain_image(
            image_path,
            mode=mode
        )

        self.history = []

    def ask(self, question):

        recent_history = self.history[-10:]

        history_text = ""

        for item in recent_history:

            history_text += (
                f"{item['role']}: "
                f"{item['content']}\n"
            )

        prompt = f"""
You are continuing a tutoring session.

ORIGINAL EXPLANATION:

{self.explanation}

CONVERSATION HISTORY:

{history_text}

CURRENT STUDENT QUESTION:

{question}

Answer naturally as a tutor.
Use previous conversation if relevant.
"""

        response = model.generate_content(prompt)

        answer = response.text

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
