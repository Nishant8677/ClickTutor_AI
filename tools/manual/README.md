# Manual scripts

These are developer scripts, not tests. They print results for a human to
read, and several of them need a display, a live Gemini API key, network
access, or keyboard input. None of them contain assertions, so none can fail
a build — which is why they no longer live in `tests/`.

Run them individually from the repository root, e.g.:

```bash
python tools/manual/test_mss.py
```

The automated suite is `tests/unit/`, run with `pytest`.

| Script | What it does | Needs |
|---|---|---|
| `run_tests.py` | Generates a lesson for every image in `tests/<category>/` and validates its structure | API key, network |
| `test_capture_engine.py` | Captures the screen via the legacy `CaptureEngine` | display |
| `test_chat.py` | Interactive chat loop — blocks on `input()` until Ctrl+C | API key, network, stdin |
| `test_coordinate_mapping.py` | Draws boxes for a human to eyeball for alignment | display |
| `test_demo_anchors.py` | Reports whether each demo anchor is findable by OCR | — |
| `test_highlighter.py` | Writes a highlighted image to disk | — |
| `test_mss.py` / `test_mss2.py` | Screen capture and monitor enumeration via mss | display |
| `test_ocr.py` | Prints the OCR word count for an image | — |
| `test_pyqt_capture.py` | Screen capture through PyQt6 | display |
| `test_recorder.py` | Records a short MP4 from the overlay | display, ffmpeg |
| `test_tutor.py` | Sends one image to Gemini and prints the reply | API key, network |

Several of these reference image paths that are not in the repository
(`sample.png`, `sample2.png`, `kth_missing.png`) and will need a path
argument or a local file before they run.
