import sys
from PyQt6.QtWidgets import QApplication

app = QApplication(sys.argv)
screen = QApplication.primaryScreen()
pixmap = screen.grabWindow(0)
pixmap.save("test_pyqt.png")
print("Saved PyQt capture")
