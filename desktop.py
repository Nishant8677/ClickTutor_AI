import logging
import sys

from PyQt6.QtWidgets import QApplication

from src.desktop.controller import DesktopController

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    app = QApplication(sys.argv)

    # Optional image to preload for OCR debugging. Normally omitted: the live
    # capture supplies the image. This used to default to "sample2.png", which
    # is not in the repository, so every launch failed to preload.
    image_path = sys.argv[1] if len(sys.argv) > 1 else None

    controller = DesktopController(default_image=image_path)
    controller.start()

    sys.exit(app.exec())
