import sys
from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtGui import QPixmap, QColor
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
