import os
from PIL import Image
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

model = genai.GenerativeModel("gemini-2.5-flash")


def explain_image(image_path, mode="student"):

    image = Image.open(image_path)

    prompts = {

        "student": """
        You are ClickTutor.

        Explain like a friendly personal tutor.

        Rules:
        - Teach from first principles.
        - Assume the student is learning for the first time.
        - Do not skip steps.
        - If information is missing, say so.

        Format:

        1. Question Summary
        2. Concepts Required
        3. Reasoning Process
        4. Step-by-Step Explanation
        5. Final Answer
        6. Common Mistakes
        """,

        "exam": """
        You are ClickTutor.

        Explain for exam preparation.

        Rules:
        - Be concise.
        - Focus on marks-scoring approach.
        - Mention formulas and shortcuts.

        Format:

        1. What is Asked
        2. Key Formula / Concept
        3. Solution
        4. Final Answer
        5. Exam Tip
        """,

        "dsa": """
        You are ClickTutor.

        Explain like a DSA interviewer and mentor.

        Rules:
        - Identify the pattern first.
        - Explain brute force.
        - Explain optimal solution.
        - Mention edge cases.
        - Mention interview pitfalls.

        Format:

        1. Problem Summary
        2. Pattern Recognition
        3. Brute Force Approach
        4. Optimal Approach
        5. Complexity Analysis
        6. Edge Cases
        7. Interview Tips
        """
    }

    prompt = prompts.get(mode, prompts["student"])

    response = model.generate_content([
        prompt,
        image
    ])

    return response.text
