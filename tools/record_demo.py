"""Records the screen to an MP4.

The existing Mp4Recorder grabs the overlay widget, which is transparent, so
outside demo playback it captures highlight shapes with none of the content
they point at. A product demo needs the real screen, which is what this does.

    python tools/record_demo.py --duration 45 --output runtime/recordings/demo.mp4

Runs standalone; tools/demo_drive.py uses it to record a scripted run.
"""

from __future__ import annotations

import argparse
import logging
import queue
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import imageio  # noqa: E402
import mss  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from src.console import configure_stdio  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_FPS = 15
# H.264 requires even dimensions. A past fix in this repo dealt with the same
# class of failure in Mp4Recorder; cropping defensively avoids it here.
MACRO_BLOCK = 2

# Encoding 1080p inline with capture only sustained about 5.5fps, so a clip
# played back roughly 2.7x too fast. Downscaling cuts the pixel count by more
# than half and 720p is ample for a portfolio clip.
DEFAULT_HEIGHT = 720

# Frames waiting to be encoded. Bounded so a slow encoder applies backpressure
# instead of growing without limit; at 720p each frame is about 2.8 MB.
QUEUE_SIZE = 90


def _even(value: int) -> int:
    return value - (value % MACRO_BLOCK)


class ScreenRecorder:
    """Captures the screen on a background thread and encodes to MP4.

    Capture runs off the main thread so a Qt event loop can drive the
    application in the foreground while recording proceeds.
    """

    def __init__(
        self,
        output: Path,
        fps: int = DEFAULT_FPS,
        monitor: int = 1,
        height: int = DEFAULT_HEIGHT,
    ) -> None:
        self.output = Path(output)
        self.fps = fps
        self.monitor = monitor
        self.height = height
        self.captured = 0
        self.encoded = 0
        self.late = 0
        self._stop = threading.Event()
        self._capture_thread: threading.Thread | None = None
        self._encode_thread: threading.Thread | None = None
        self._queue: queue.Queue = queue.Queue(maxsize=QUEUE_SIZE)
        self.error: Exception | None = None
        self._started_at = 0.0
        self._finished_at = 0.0

    def start(self) -> None:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self._stop.clear()
        self.captured = self.encoded = self.late = 0
        self._started_at = time.perf_counter()
        # Capture and encode run on separate threads: encoding a frame costs
        # far more than grabbing one, and doing both inline held capture to
        # about 5.5fps, so clips played back nearly 3x too fast.
        self._encode_thread = threading.Thread(target=self._encode_loop, daemon=True)
        self._capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._encode_thread.start()
        self._capture_thread.start()
        logger.info("Recording at %sfps, %sp -> %s", self.fps, self.height, self.output)

    def _capture_loop(self) -> None:
        interval = 1.0 / self.fps
        try:
            # mss instances are not thread-safe, so this one belongs to the
            # capture thread and is never shared.
            with mss.mss() as sct:
                region = sct.monitors[self.monitor]
                scale = self.height / region["height"]
                size = (_even(int(region["width"] * scale)), _even(self.height))

                while not self._stop.is_set():
                    started = time.perf_counter()
                    shot = sct.grab(region)
                    frame = np.frombuffer(shot.bgra, dtype=np.uint8).reshape(
                        shot.height, shot.width, 4
                    )
                    # BGRA -> RGB, then downscale for a cheaper encode.
                    rgb = Image.fromarray(frame[:, :, 2::-1]).resize(
                        size, Image.Resampling.BILINEAR
                    )
                    try:
                        self._queue.put(np.asarray(rgb), timeout=1.0)
                        self.captured += 1
                    except queue.Full:
                        self.late += 1

                    remaining = interval - (time.perf_counter() - started)
                    if remaining > 0:
                        time.sleep(remaining)
                    else:
                        self.late += 1
        except Exception as exc:
            self.error = exc
            logger.exception("Screen capture failed")
        finally:
            self._queue.put(None)  # sentinel: no more frames

    def _encode_loop(self) -> None:
        try:
            with imageio.get_writer(
                self.output,
                fps=self.fps,
                codec="libx264",
                quality=7,
                macro_block_size=None,
                ffmpeg_params=["-preset", "veryfast"],
            ) as writer:
                while True:
                    frame = self._queue.get()
                    if frame is None:
                        break
                    writer.append_data(frame)
                    self.encoded += 1
        except Exception as exc:
            self.error = exc
            logger.exception("Encoding failed")

    def stop(self) -> Path:
        """Stops capture, drains the queue and finalises the file.

        Returns:
            The path written.

        Raises:
            RuntimeError: If capture failed or produced no frames.
        """
        self._stop.set()
        if self._capture_thread:
            self._capture_thread.join(timeout=15)
        if self._encode_thread:
            self._encode_thread.join(timeout=60)
        self._finished_at = time.perf_counter()

        if self.error:
            raise RuntimeError(f"recording failed: {self.error}") from self.error
        if not self.encoded:
            raise RuntimeError("no frames captured")

        wall = self._finished_at - self._started_at
        achieved = self.encoded / wall if wall else 0
        # Playback speed is wrong if capture could not sustain the target rate,
        # which is the failure that made an early clip run 2.7x fast.
        if achieved < self.fps * 0.9:
            logger.warning(
                "Captured %.1ffps against a %sfps target; playback will look "
                "%.1fx fast. Lower --fps or --height.",
                achieved,
                self.fps,
                self.fps / achieved if achieved else 0,
            )

        logger.info(
            "Wrote %s (%.1f MB, %s frames, %.1fs wall, %.1ffps achieved)",
            self.output,
            self.output.stat().st_size / 1e6,
            self.encoded,
            wall,
            achieved,
        )
        return self.output


def main() -> int:
    configure_stdio()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=30.0, help="seconds")
    parser.add_argument("--output", default="runtime/recordings/demo.mp4")
    parser.add_argument("--fps", type=int, default=DEFAULT_FPS)
    parser.add_argument("--monitor", type=int, default=1)
    parser.add_argument(
        "--height",
        type=int,
        default=DEFAULT_HEIGHT,
        help="output height; lower is cheaper to encode",
    )
    args = parser.parse_args()

    recorder = ScreenRecorder(
        Path(args.output), fps=args.fps, monitor=args.monitor, height=args.height
    )
    recorder.start()
    try:
        time.sleep(args.duration)
    except KeyboardInterrupt:
        print("\nstopping early…")
    recorder.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
