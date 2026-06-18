from PIL import Image
import google.generativeai as genai
from dotenv import load_dotenv
import os

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")

genai.configure(api_key=api_key)

model = genai.GenerativeModel("gemini-2.5-flash")

image = Image.open("sample.png")

response = model.generate_content([
    "Explain this image like a tutor. Be detailed and beginner-friendly.",
    image
])

print("\n===== GEMINI RESPONSE =====\n")
print(response.text)
