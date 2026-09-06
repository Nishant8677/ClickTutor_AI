import json
import logging
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from src.lesson_engine import parse_lesson_steps
from src.ocr_locator import extract_ocr_data

logger = logging.getLogger(__name__)

# Anchor the packaged demos to the project root (the directory containing
# ``src/`` and ``demo/``), not the shell's CWD. Launching desktop.py/run.ps1
# from another folder used to leave the demo dropdown empty because the old
# relative "demo" default resolved against wherever the user happened to be.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEMOS_DIR = PROJECT_ROOT / "demo"
STEP_DURATION_MS = 6000  # 6.0 seconds per step


class DemoManager(QObject):
    demo_started = pyqtSignal(str)
    demo_stopped = pyqtSignal()
    step_changed = pyqtSignal(dict, dict)  # ocr_data, step_dict

    def __init__(self, capture_engine, demos_dir=DEMOS_DIR):
        super().__init__()
        self.capture_engine = capture_engine
        self.demos_dir = Path(demos_dir)
        self.demos = self._load_demos()

        self.is_running = False
        self.current_lesson_steps = []
        self.current_step_index = 0
        self.ocr_data = None

        self.step_timer = QTimer(self)
        self.step_timer.timeout.connect(self._next_step)
        self.step_timer.setInterval(STEP_DURATION_MS)

    def _load_demos(self):
        """
        Scans the demos directory for self-contained demo packages.
        Each package is a subdirectory containing a lesson.json file.
        """
        demos = {}
        if not self.demos_dir.is_dir():
            return demos

        for package_dir in sorted(self.demos_dir.iterdir()):
            if not package_dir.is_dir():
                continue

            lesson_path = package_dir / "lesson.json"
            if not lesson_path.is_file():
                continue

            try:
                with lesson_path.open(encoding="utf-8") as f:
                    demos[package_dir.name] = json.load(f)
            except Exception as e:
                logger.warning("Failed to load demo '%s': %s", package_dir.name, e)

        return demos

    def get_available_demos(self):
        """Returns a dict of demo_id -> metadata."""
        return {k: v.get("metadata", {}) for k, v in self.demos.items()}

    def resolve_screenshot_path(self, demo_id):
        """Return the absolute screenshot path for a loaded demo.

        Resolution rule, independent of the process CWD:
          1. No ``screenshot`` field -> ``<package dir>/screenshot.png``.
          2. Absolute path -> used as-is.
          3. Relative path -> tried against the project root first (the
             shipped ``demo/<id>/screenshot.png`` form), then against the demo
             package directory. If neither exists, the project-root candidate
             is returned so the OCR failure names a concrete file.
        """
        package_dir = self.demos_dir / demo_id
        raw = self.demos[demo_id].get("screenshot")
        if not raw:
            return str((package_dir / "screenshot.png").resolve())

        raw_path = Path(raw)
        if raw_path.is_absolute():
            return str(raw_path)

        root_candidate = (PROJECT_ROOT / raw_path).resolve()
        if root_candidate.is_file():
            return str(root_candidate)

        package_candidate = (package_dir / raw_path).resolve()
        if package_candidate.is_file():
            return str(package_candidate)

        return str(root_candidate)

    def start_demo(self, demo_id):
        if demo_id not in self.demos:
            logger.warning("Demo '%s' not found.", demo_id)
            return

        demo_data = self.demos[demo_id]
        image_path = self.resolve_screenshot_path(demo_id)

        try:
            self.ocr_data = extract_ocr_data(image_path)
        except Exception as e:
            logger.error("Failed to extract OCR for demo '%s': %s", demo_id, e)
            return

        self.current_lesson_steps = parse_lesson_steps(demo_data.get("lesson_text", ""))
        self.current_step_index = -1
        self.is_running = True

        self.demo_started.emit(image_path)
        self._next_step()  # Trigger first step immediately
        self.step_timer.start()

    def stop_demo(self):
        if not self.is_running:
            return

        self.is_running = False
        self.step_timer.stop()
        self.current_lesson_steps = []
        self.demo_stopped.emit()

    def _next_step(self):
        if not self.is_running:
            return

        self.current_step_index += 1
        if self.current_step_index >= len(self.current_lesson_steps):
            self.stop_demo()
            return

        step_data = self.current_lesson_steps[self.current_step_index]
        self.step_changed.emit(self.ocr_data, step_data)
