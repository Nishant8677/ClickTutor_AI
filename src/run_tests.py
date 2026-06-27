import sys
import os
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.chat_tutor import TutorSession
from src.lesson_validator import validate_lesson_steps

def run_screenshot_test_suite():
    tests_dir = Path("tests")
    if not tests_dir.exists():
        print("❌ Error: 'tests' directory not found.")
        return False

    print("==================================================")
    print("🚀 Starting ClickTutor Screenshot Test Suite")
    print("==================================================")

    categories = [d for d in tests_dir.iterdir() if d.is_dir()]
    total_tests = 0
    passed_tests = 0
    results = []

    for cat in categories:
        cat_name = cat.name
        image_files = list(cat.glob("*.png")) + list(cat.glob("*.jpg")) + list(cat.glob("*.jpeg"))
        
        if not image_files:
            continue

        print(f"\n📂 Category: {cat_name.upper()} ({len(image_files)} image(s))")
        print("-" * 50)

        for img_path in image_files:
            total_tests += 1
            print(f"📸 Testing {img_path.name}...")
            
            try:
                # 1. Initialize session (forces classification and baseline OCR)
                session = TutorSession(str(img_path), mode="student")
                classification = session.screenshot_type
                
                # 2. Ask test question
                test_question = "Explain what is on the screen and how to work with it."
                answer, highlighted_image, lesson_steps = session.ask(test_question)
                
                # 3. Validate structured steps
                is_valid, errors = validate_lesson_steps(lesson_steps)
                
                if is_valid:
                    passed_tests += 1
                    status = "✅ PASS"
                    err_msg = ""
                    print(f"  └─ Status: {status} (Classified as: {classification})")
                else:
                    status = "❌ FAIL"
                    err_msg = "; ".join(errors)
                    print(f"  └─ Status: {status} (Errors: {err_msg})")

                results.append({
                    "category": cat_name,
                    "file": img_path.name,
                    "classification": classification,
                    "status": status,
                    "errors": err_msg
                })

            except Exception as e:
                status = "❌ ERROR"
                err_msg = str(e)
                print(f"  └─ Status: {status} (Exception: {err_msg})")
                results.append({
                    "category": cat_name,
                    "file": img_path.name,
                    "classification": "unknown",
                    "status": status,
                    "errors": err_msg
                })

    print("\n" + "=" * 50)
    print("📊 TEST SUITE SUMMARY")
    print("=" * 50)
    print(f"Total Tests: {total_tests}")
    print(f"Passed:      {passed_tests} / {total_tests}")
    print(f"Failed:      {total_tests - passed_tests}")
    print("-" * 50)
    
    for res in results:
        print(f"[{res['status']}] {res['category']}/{res['file']} -> Class: '{res['classification']}' {res['errors']}")
    print("==================================================")
    
    return passed_tests == total_tests

if __name__ == "__main__":
    success = run_screenshot_test_suite()
    sys.exit(0 if success else 1)
