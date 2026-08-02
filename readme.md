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
│   │   ├── coordinates.py   # Image-space to widget-space transform
│   │   ├── overlay.py
│   │   ├── renderer.py
│   │   └── shapes.py
│   ├── capture/             # In-memory screen capture
│   │   └── screen_capture.py
│   ├── input/               # Hotkeys, actions, tutor state machine
│   │   ├── events.py
│   │   ├── hotkeys.py
│   │   ├── input_manager.py
│   │   └── state_machine.py
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
├── tests/
│   ├── unit/                # Automated pytest suite (deterministic)
│   └── code|math|diagrams/  # Test images by category
├── tools/                   # Developer utilities
│   ├── benchmark.py
│   └── manual/              # Interactive scripts (not tests)
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
Copy the template and fill in your key from
[Google AI Studio](https://aistudio.google.com/apikey):
```bash
cp .env.example .env
```
```
GEMINI_API_KEY=your_api_key_here
```

---

## Running ClickTutor

### Desktop Overlay (Primary Mode)
```bash
python desktop.py
```

> **Run this natively, not from WSL.** Under WSLg, Qt renders into the WSLg
> display server while screen capture reads the Windows desktop — two
> different displays — so the overlay cannot draw on top of Windows
> applications. WSL is fine for the Streamlit mode and for the test suite.

Optionally pass an image to preload for OCR debugging:
```bash
python desktop.py path/to/screenshot.png
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

The automated suite is deterministic and needs no display, network, or API key:

```bash
pip install -r requirements-dev.txt
pytest
```

Lint and type checks (also run in CI):

```bash
ruff check .
mypy src/attention/coordinates.py src/lesson_validator.py
```

`tools/manual/` holds developer scripts that print results for a human to
read — several need a display, an API key, or keyboard input. They contain no
assertions and are not collected by pytest. See `tools/manual/README.md`.

Place test images for `tools/manual/run_tests.py` in `tests/` subdirectories
organized by category (e.g. `tests/code/`, `tests/math/`).

---

## Demo Packages

Demos live in `demo/`. Each is a self-contained folder with:
- `lesson.json` — the structured lesson text and screenshot reference
- `screenshot.png` — the static image used during demo playback

---

## License
MIT
