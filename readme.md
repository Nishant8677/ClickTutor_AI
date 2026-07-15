# ClickTutor AI

An AI-powered visual tutoring tool that watches your screen, understands what you're looking at, and explains it step-by-step — pointing directly at what matters.

---

## Features

- **Screen-Aware Lessons** — Captures your screen and generates a structured lesson with OCR-anchored annotations.
- **Attention Engine** — Draws animated circles, rectangles, and underlines directly on your screen to highlight key elements.
- **Demo Mode** — Plays offline pre-built lessons with animated step-by-step walkthroughs.
- **Streamlit UI** — Upload a screenshot and ask questions via a web interface.
- **Desktop Overlay** — A transparent PyQt6 overlay that sits above all windows.

---

## Project Structure

```
ClickTutor_AI/
│
├── src/                     # Application source code
│   ├── attention/           # Overlay rendering engine
│   │   ├── animation.py
│   │   ├── overlay.py
│   │   ├── renderer.py
│   │   └── shapes.py
│   ├── desktop/             # Desktop app (PyQt6)
│   │   ├── capture.py
│   │   ├── capture.ps1
│   │   ├── controller.py
│   │   ├── demo_manager.py
│   │   └── recorder.py
│   ├── chat_tutor.py        # Streamlit session wrapper
│   ├── highlighter.py       # PIL image annotation utility
│   ├── lesson_engine.py     # Core lesson generation engine
│   ├── lesson_validator.py  # Lesson step validation
│   ├── ocr_locator.py       # OCR extraction and text locator
│   ├── screenshot_classifier.py
│   └── tutor.py             # Gemini model configuration
│
├── demo/                    # Self-contained demo packages
│   ├── kth_missing/
│   │   ├── lesson.json
│   │   └── screenshot.png
│   └── rotate_image/
│       ├── lesson.json
│       └── screenshot.png
│
├── tests/                   # Test scripts
├── tools/                   # Developer utilities (benchmark, etc.)
├── benchmarks/              # Benchmark results and charts
├── assets/                  # Static assets
│   └── test_images/
├── runtime/                 # Runtime-generated files (gitignored)
│   ├── captures/
│   ├── highlights/
│   ├── recordings/
│   ├── temp/
│   └── logs/
├── archive/                 # Archived experimental scripts
├── scripts/                 # Automation scripts (install, build)
├── docs/                    # Documentation
│
├── app.py                   # Streamlit web app entry point
├── desktop.py               # Desktop overlay entry point
├── requirements.txt         # Runtime dependencies
└── requirements-dev.txt     # Development-only dependencies
```

---

## Installation

### 1. Clone the repository
```bash
git clone <repo-url>
cd ClickTutor_AI
```

### 2. Create a virtual environment
```bash
python -m venv venv
source venv/bin/activate  # Linux/macOS
# or
venv\Scripts\activate  # Windows
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Install Tesseract OCR
- **Ubuntu/WSL:** `sudo apt install tesseract-ocr`
- **Windows:** Download from [UB Mannheim](https://github.com/UB-Mannheim/tesseract/wiki)

### 5. Set up your API key
Create a `.env` file in the project root:
```
GEMINI_API_KEY=your_api_key_here
```

---

## Running ClickTutor

### Desktop Overlay (Primary Mode)
```bash
python desktop.py
```

### Streamlit Web App
```bash
streamlit run app.py
```

### Benchmark Tool
```bash
pip install -r requirements-dev.txt
python tools/benchmark.py
```

---

## Running Tests
```bash
python tests/run_tests.py
```
Place test images in `tests/` subdirectories organized by category (e.g., `tests/code/`, `tests/math/`).

---

## Demo Packages

Demos live in `demo/`. Each is a self-contained folder with:
- `lesson.json` — the structured lesson text and screenshot reference
- `screenshot.png` — the static image used during demo playback

---

## License
MIT
