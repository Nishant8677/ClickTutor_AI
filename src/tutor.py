import os
from PIL import Image
from dotenv import load_dotenv
import google.generativeai as genai


load_dotenv()

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

model = genai.GenerativeModel("gemini-2.5-flash")


def explain_image(image_path):

    image = Image.open(image_path)

    prompt = """
    You are ClickTutor.

    Explain the screenshot like a personal tutor.

    Structure your answer as:

    1. What is being asked?
    2. Concept involved
    3. Step-by-step explanation
    4. Final answer
    5. Common mistakes students make
    """

    response = model.generate_content([prompt, image])

    return response.text
