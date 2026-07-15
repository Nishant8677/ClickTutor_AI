import os
import json
import logging
from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from src.lesson_engine import parse_lesson_steps
from src.ocr_locator import extract_ocr_data

logger = logging.getLogger(__name__)

DEMOS_DIR = "demo"
STEP_DURATION_MS = 6000  # 6.0 seconds per step


class DemoManager(QObject):
    demo_started = pyqtSignal(str)
    demo_stopped = pyqtSignal()
    step_changed = pyqtSignal(dict, dict)  # ocr_data, step_dict

    def __init__(self, capture_engine, demos_dir=DEMOS_DIR):
        super().__init__()
        self.capture_engine = capture_engine
        self.demos_dir = demos_dir
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
        if not os.path.exists(self.demos_dir):
            return demos

        for entry in os.scandir(self.demos_dir):
            if not entry.is_dir():
                continue

            lesson_path = os.path.join(entry.path, "lesson.json")
            if not os.path.exists(lesson_path):
                continue

            try:
                with open(lesson_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    demos[entry.name] = data
            except Exception as e:
                logger.warning("Failed to load demo '%s': %s", entry.name, e)

        return demos

    def get_available_demos(self):
        """Returns a dict of demo_id -> metadata."""
        return {k: v.get("metadata", {}) for k, v in self.demos.items()}

    def start_demo(self, demo_id):
        if demo_id not in self.demos:
            logger.warning("Demo '%s' not found.", demo_id)
            return

        demo_data = self.demos[demo_id]
        image_path = demo_data.get("screenshot", f"demo/{demo_id}/screenshot.png")

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
