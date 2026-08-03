"""Runs ClickTutor end to end unattended and records it to MP4.

Produces the portfolio demo without a human at the keyboard: it triggers a
capture, supplies the question, waits for the lesson, walks the steps, and
stops. Everything goes through InputManager, so what is recorded is the real
application, not a mock.

    python tools/demo_drive.py --question "What does this function do?"

The question dialog is answered by patching QInputDialog.getText. That dialog
is modal and would otherwise block the event loop this script drives.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtCore import QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QInputDialog  # noqa: E402

from src.console import configure_stdio  # noqa: E402
from src.desktop.controller import DesktopController  # noqa: E402
from src.input import InputAction, TutorState  # noqa: E402
from tools.record_demo import ScreenRecorder  # noqa: E402

logger = logging.getLogger(__name__)

LEAD_IN_MS = 2500  # idle companion on screen before anything happens
STEP_DWELL_MS = 5000  # time to read each step
TAIL_MS = 2500  # hold after the last step
POLL_MS = 250
LESSON_TIMEOUT_MS = 90_000
# Time to alt-tab to the window being demoed before the first frame.
DEFAULT_PREPARE_SECONDS = 8


class DemoDriver:
    """Sequences a full lesson and records it."""

    def __init__(
        self,
        controller,
        app,
        question,
        output,
        fps,
        step_dwell_ms,
        prepare_seconds=DEFAULT_PREPARE_SECONDS,
    ):
        self.c = controller
        self.app = app
        self.question = question
        self.recorder = ScreenRecorder(Path(output), fps=fps)
        self.step_dwell_ms = step_dwell_ms
        self.prepare_seconds = prepare_seconds
        self.waited_ms = 0
        self.failed: str | None = None

    def run(self) -> int:
        # Answer the modal question dialog without a human.
        QInputDialog.getText = lambda *a, **kw: (self.question, True)

        self.c.start()

        # Recording starts only after this, so switching to the window you want
        # demoed does not end up in the video. The app is already running, so
        # the companion is on screen and settled before the first frame.
        if self.prepare_seconds > 0:
            print(
                f"\n  Switch to the window you want in the demo. "
                f"Recording starts in {self.prepare_seconds}s.\n",
                flush=True,
            )
            for remaining in range(self.prepare_seconds, 0, -1):
                print(f"    {remaining}…", end="\r", flush=True)
                time.sleep(1)
            print("    recording now          ", flush=True)

        self.recorder.start()
        logger.info("Lead-in…")
        QTimer.singleShot(LEAD_IN_MS, self._trigger_capture)
        self.app.exec()

        try:
            path = self.recorder.stop()
        except RuntimeError as exc:
            logger.error("Recording failed: %s", exc)
            return 1

        if self.failed:
            logger.error("Demo failed: %s", self.failed)
            print(f"\nRECORDED ANYWAY: {path}")
            return 1

        print(f"\nDEMO RECORDED: {path}")
        return 0

    def _trigger_capture(self) -> None:
        logger.info("Triggering capture…")
        self.c.input_manager.handle_action(InputAction.CAPTURE_SCREEN)
        QTimer.singleShot(POLL_MS, self._await_lesson)

    def _await_lesson(self) -> None:
        state = self.c.input_manager.current_state
        if state in (TutorState.TEACHING, TutorState.FINISHED) and self.c.lesson_steps:
            logger.info("Lesson ready: %s steps", len(self.c.lesson_steps))
            QTimer.singleShot(self.step_dwell_ms, self._next_step)
            return

        self.waited_ms += POLL_MS
        if self.waited_ms >= LESSON_TIMEOUT_MS:
            self.failed = f"no lesson after {LESSON_TIMEOUT_MS / 1000:.0f}s (state {state.name})"
            self._finish()
            return
        if state == TutorState.IDLE and self.waited_ms > LEAD_IN_MS:
            # The controller resets to IDLE on error, so this is a real failure
            # rather than the lesson still being in flight.
            self.failed = "returned to IDLE without producing a lesson"
            self._finish()
            return
        QTimer.singleShot(POLL_MS, self._await_lesson)

    def _next_step(self) -> None:
        index = self.c.current_step_index
        if index >= len(self.c.lesson_steps) - 1:
            logger.info("Last step reached; holding…")
            QTimer.singleShot(TAIL_MS, self._finish)
            return

        logger.info("Advancing to step %s", index + 2)
        self.c.input_manager.handle_action(InputAction.NEXT_STEP)
        QTimer.singleShot(self.step_dwell_ms, self._next_step)

    def _finish(self) -> None:
        self.c.input_manager.handle_action(InputAction.CANCEL_LESSON)
        QTimer.singleShot(400, self.app.quit)


def main() -> int:
    configure_stdio()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question", default="What does this code do?")
    parser.add_argument("--output", default="runtime/recordings/clicktutor_demo.mp4")
    parser.add_argument("--fps", type=int, default=15)
    parser.add_argument("--step-dwell", type=int, default=STEP_DWELL_MS, help="ms per step")
    parser.add_argument(
        "--prepare",
        type=int,
        default=DEFAULT_PREPARE_SECONDS,
        help="seconds to switch windows before recording starts",
    )
    args = parser.parse_args()

    app = QApplication(sys.argv)
    controller = DesktopController(show_dev_panel=False)
    driver = DemoDriver(
        controller,
        app,
        args.question,
        args.output,
        args.fps,
        args.step_dwell,
        prepare_seconds=args.prepare,
    )
    return driver.run()


if __name__ == "__main__":
    sys.exit(main())
