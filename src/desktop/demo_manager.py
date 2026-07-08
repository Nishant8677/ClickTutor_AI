import os
import json
from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from src.lesson_engine import parse_lesson_steps
from src.ocr_locator import extract_ocr_data

class DemoManager(QObject):
    demo_started = pyqtSignal()
    demo_stopped = pyqtSignal()
    step_changed = pyqtSignal(dict, dict) # ocr_data, step_dict

    def __init__(self, capture_engine, demos_dir="src/desktop/demos"):
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
        self.step_duration_ms = 6000 # 6.0 seconds per step

    def _load_demos(self):
        demos = {}
        if not os.path.exists(self.demos_dir):
            return demos
            
        for filename in os.listdir(self.demos_dir):
            if filename.endswith(".json"):
                path = os.path.join(self.demos_dir, filename)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        demo_id = filename.replace(".json", "")
                        demos[demo_id] = data
                except Exception as e:
                    print(f"Failed to load demo {filename}: {e}")
        return demos

    def get_available_demos(self):
        """Returns a dict of demo_id -> metadata"""
        return {k: v.get("metadata", {}) for k, v in self.demos.items()}

    def start_demo(self, demo_id):
        if demo_id not in self.demos:
            print(f"Demo {demo_id} not found.")
            return

        demo_data = self.demos[demo_id]
        
        # In a real environment we might capture the screen here:
        # self.capture_engine.capture(target="screen")
        # But for offline deterministic demos, we use the predefined screenshot
        image_path = demo_data.get("screenshot", "sample2.png")
        
        try:
            self.ocr_data = extract_ocr_data(image_path)
        except Exception as e:
            print(f"Error extracting OCR for demo: {e}")
            return
            
        self.current_lesson_steps = parse_lesson_steps(demo_data.get("lesson_text", ""))
        self.current_step_index = -1
        self.is_running = True
        
        self.demo_started.emit()
        self._next_step() # Trigger first step immediately
        self.step_timer.start(self.step_duration_ms)

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
            # End of demo
            self.stop_demo()
            return
            
        step_data = self.current_lesson_steps[self.current_step_index]
        self.step_changed.emit(self.ocr_data, step_data)
