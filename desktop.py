"""Entry point for the ClickTutor desktop overlay.

Must run on native Windows (or native Linux). Under WSL the overlay is drawn
into the WSLg display server while capture reads the Windows desktop, so
highlights cannot appear over Windows applications — see check_environment().
"""

import logging
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent


def _venv_hint() -> str:
    """Points at the interpreter that actually has the dependencies."""
    windows_venv = REPO_ROOT / "venv-win" / "Scripts" / "python.exe"
    if windows_venv.exists():
        return f'  "{windows_venv}" desktop.py'
    return (
        "  py -m venv venv-win\n"
        "  .\\venv-win\\Scripts\\pip install -r requirements.txt\n"
        "  .\\venv-win\\Scripts\\python.exe desktop.py"
    )


def check_environment() -> None:
    """Fails early and specifically rather than on an opaque ImportError.

    Three interpreters are usually present on this project — global Python,
    the native Windows venv, and the WSL venv — and only one of them can run
    the overlay. A bare ModuleNotFoundError does not make that obvious.
    """
    try:
        import PyQt6.QtWidgets  # noqa: F401
    except ModuleNotFoundError as exc:
        sys.exit(
            f"ClickTutor cannot start: '{exc.name}' is not installed for this "
            f"interpreter.\n\n"
            f"  interpreter: {sys.executable}\n\n"
            f"The desktop overlay needs the native Windows environment:\n"
            f"{_venv_hint()}\n"
        )

    # WSL detection: os.uname is absent on Windows, so guard before calling it.
    if hasattr(os, "uname") and "microsoft" in os.uname().release.lower():
        logging.getLogger(__name__).warning(
            "Running under WSL. Qt renders into the WSLg display while capture "
            "reads the Windows desktop, so highlights will not appear over "
            "Windows applications. Run from the native Windows venv instead:\n"
            "%s",
            _venv_hint(),
        )


if __name__ == "__main__":
    from src.console import configure_stdio

    # Before logging is configured: log records carry OCR'd screen text and
    # model output, which on Windows would otherwise hit a cp1252 stream.
    configure_stdio()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    check_environment()

    from PyQt6.QtWidgets import QApplication

    from src.desktop.controller import DesktopController

    app = QApplication(sys.argv)

    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    # M7: the learner sees only the overlay and the companion. The developer
    # panel -- demo dropdown, MP4 recorder, debug toggle -- is opt-in, so a
    # recording never has to be cropped around it.
    show_dev_panel = "--dev" in sys.argv

    # Optional image to preload for OCR debugging. Normally omitted: the live
    # capture supplies the image. This used to default to "sample2.png", which
    # is not in the repository, so every launch failed to preload.
    image_path = args[0] if args else None

    controller = DesktopController(default_image=image_path, show_dev_panel=show_dev_panel)
    controller.start()

    sys.exit(app.exec())
