import logging
import os
import subprocess
import tempfile

import mss
from PIL import Image

from src.attention.coordinates import validate_region

logger = logging.getLogger(__name__)

# wslpath is instant; the PowerShell bridge spawns a process and writes a
# full-screen PNG, so it gets a wider budget. Neither may block forever.
SUBPROCESS_TIMEOUT_SECONDS = 10
POWERSHELL_TIMEOUT_SECONDS = 30


class CaptureError(RuntimeError):
    """A screen grab could not honour what was asked of it."""


class RegionUnsupportedError(CaptureError):
    """An explicit region was requested on a path that can only grab full screens."""


class CaptureSizeMismatchError(CaptureError):
    """The grabbed image is not the size the requested region describes."""


class ScreenCapture:
    def __init__(self):
        pass

    def _is_wsl(self) -> bool:
        # os.uname() does not exist on Windows, so it must not be reached
        # before the platform check or the native path dies with AttributeError.
        if not hasattr(os, "uname"):
            return False
        return "WSL" in os.uname().release or os.path.exists("/run/WSL")

    def supports_regions(self) -> bool:
        """True when an explicit ``region`` will be honoured rather than refused."""
        return not self._is_wsl()

    def capture(self, monitor_index: int = 1, region=None) -> Image.Image:
        """Captures the screen and returns a PIL Image in memory.

        Args:
            monitor_index: Index into mss's monitor list, used when no explicit
                region is given. Ignored on the WSL fallback path.
            region: Optional ``{"left", "top", "width", "height"}`` in physical
                desktop pixels, e.g. a screen's bounds or
                :meth:`CaptureGeometry.region`. ``None`` means the whole
                monitor. Anything else is validated before any grab; an empty
                or malformed dict is an error, never "full screen".

        Returns:
            An RGB PIL Image exactly ``region`` in size when a region is given.

        Raises:
            ValueError: If ``region`` is not None and is malformed.
            RegionUnsupportedError: If a region is requested on the WSL
                fallback path, which can only grab the Windows primary screen.
            CaptureSizeMismatchError: If the grabbed image's size differs from
                the requested region.
        """
        if region is not None:
            region = validate_region(region)
            if not self.supports_regions():
                raise RegionUnsupportedError(
                    "Region capture is not available on the WSL fallback path; "
                    "it can only grab the full Windows primary screen"
                )

        if self._is_wsl():
            # In WSL, mss cannot capture the Windows screen due to X11 boundaries.
            # We must use a powershell fallback for development.
            # We use a temp file to bridge the gap, then read it into memory and delete it.
            # This satisfies the "no permanent temp files" requirement while keeping WSL working.
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
                temp_path = tf.name

            # try/finally: without it, any failure between here and the read
            # leaked a full screenshot of the user's desktop to /tmp forever.
            try:
                script_path = os.path.abspath(
                    os.path.join(os.path.dirname(__file__), "..", "desktop", "capture.ps1")
                )
                win_output_path = (
                    subprocess.check_output(
                        ["wslpath", "-w", temp_path], timeout=SUBPROCESS_TIMEOUT_SECONDS
                    )
                    .decode()
                    .strip()
                )
                win_script_path = (
                    subprocess.check_output(
                        ["wslpath", "-w", script_path], timeout=SUBPROCESS_TIMEOUT_SECONDS
                    )
                    .decode()
                    .strip()
                )

                logger.info("WSL detected. Running capture.ps1 fallback...")
                # Timed out rather than left to block: a hung powershell.exe
                # would otherwise wedge the calling thread indefinitely.
                subprocess.run(
                    [
                        "powershell.exe",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        win_script_path,
                        win_output_path,
                    ],
                    check=True,
                    timeout=POWERSHELL_TIMEOUT_SECONDS,
                )

                img = Image.open(temp_path)
                img.load()  # Force load into memory before closing/deleting file
                return img
            finally:
                try:
                    os.remove(temp_path)
                except OSError as exc:
                    logger.warning("Could not remove temp capture %r: %s", temp_path, exc)
        else:
            with mss.mss() as sct:
                if region is not None:
                    monitor = region
                    logger.info(
                        "Native capture via mss of region %sx%s at (%s, %s)",
                        monitor["width"],
                        monitor["height"],
                        monitor["left"],
                        monitor["top"],
                    )
                else:
                    if not 0 <= monitor_index < len(sct.monitors):
                        raise IndexError(
                            f"Monitor index {monitor_index} out of range; "
                            f"mss reports {len(sct.monitors)} entries"
                        )
                    monitor = sct.monitors[monitor_index]
                    logger.info(
                        f"Native capture via mss on monitor {monitor_index} ({monitor['width']}x{monitor['height']})"
                    )
                sct_img = sct.grab(monitor)
                # Convert to PIL Image (mss returns BGRA)
                img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                if region is not None and img.size != (region["width"], region["height"]):
                    # Never hand back something else: geometry built for the
                    # request would place every highlight against the wrong pixels.
                    raise CaptureSizeMismatchError(
                        f"Requested {region['width']}x{region['height']} but the grab "
                        f"returned {img.width}x{img.height}"
                    )
                return img
