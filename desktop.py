import sys
from PyQt6.QtWidgets import QApplication
from src.desktop.controller import DesktopController

if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # Use image passed in args, or default to sample2.png
    image_path = "sample2.png"
    if len(sys.argv) > 1:
        image_path = sys.argv[1]
        
    controller = DesktopController(default_image=image_path)
    controller.start()
    
    sys.exit(app.exec())

