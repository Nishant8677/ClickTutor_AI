import sys
import os
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.chat_tutor import TutorSession
from src.lesson_validator import validate_lesson_steps

def test_tutor_pipeline():
    sample_img = "sample2.png"
    if not os.path.exists(sample_img):
        print(f"❌ Test aborted: {sample_img} does not exist.")
        return False

    print("🚀 Starting ClickTutor pipeline validation test...")
    print(f"📸 Image: {sample_img}")
    
    session = TutorSession(sample_img, mode="student")
    print(f"✅ Screenshot classified as: {session.screenshot_type}")
    print("🧠 Generated initial explanation successfully.")

    test_question = "Explain this code and how it works."
    print(f"💬 Asking question: '{test_question}'...")
    
    answer, highlighted_image, lesson_steps = session.ask(test_question)
    
    print("\n=== ClickTutor Output ===")
    print(answer)
    print("=========================\n")

    print("🔍 Validating lesson steps...")
    is_valid, errors = validate_lesson_steps(lesson_steps)
    
    if is_valid:
        print("✅ SUCCESS: All lesson steps are valid and structured correctly!")
        return True
    else:
        print("❌ FAILED: Lesson validation errors detected:")
        for err in errors:
            print(f"  - {err}")
        return False

if __name__ == "__main__":
    success = test_tutor_pipeline()
    sys.exit(0 if success else 1)
