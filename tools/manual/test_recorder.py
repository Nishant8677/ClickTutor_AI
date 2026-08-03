import sys
from pathlib import Path

# Running this file directly puts tools/manual on sys.path, not the repo
# root, so "src" would not be importable without this.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import sys

from PyQt6.QtWidgets import QApplication, QWidget

from src.desktop.recorder import Mp4Recorder

app = QApplication(sys.argv)

# Create a dummy widget
widget = QWidget()
widget.resize(101, 100)
widget.setStyleSheet("background-color: red;")
widget.show()

# Dummy recorder
recorder = Mp4Recorder(overlay=widget, fps=15)
recorder.start_recording()

# Manually trigger a few frames
for _ in range(5):
    recorder._capture_frame()

recorder.stop_recording("test_output.mp4")
print("Test completed.")
