import mss
from PIL import Image
import os
import subprocess
import tempfile
import logging

logger = logging.getLogger(__name__)

class ScreenCapture:
    def __init__(self):
        pass

    def _is_wsl(self):
        return "WSL" in os.uname().release or os.path.exists("/run/WSL")

    def capture(self, monitor_index=1) -> Image.Image:
        """
        Captures the screen and returns a PIL Image in memory.
        """
        if self._is_wsl():
            # In WSL, mss cannot capture the Windows screen due to X11 boundaries.
            # We must use a powershell fallback for development.
            # We use a temp file to bridge the gap, then read it into memory and delete it.
            # This satisfies the "no permanent temp files" requirement while keeping WSL working.
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
                temp_path = tf.name
                
            script_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "desktop", "capture.ps1"))
            win_output_path = subprocess.check_output(["wslpath", "-w", temp_path]).decode().strip()
            win_script_path = subprocess.check_output(["wslpath", "-w", script_path]).decode().strip()
            
            logger.info("WSL detected. Running capture.ps1 fallback...")
            subprocess.run([
                "powershell.exe", "-ExecutionPolicy", "Bypass",
                "-File", win_script_path, win_output_path
            ], check=True)
            
            img = Image.open(temp_path)
            img.load()  # Force load into memory before closing/deleting file
            os.remove(temp_path)
            return img
        else:
            with mss.mss() as sct:
                monitor = sct.monitors[monitor_index]
                logger.info(f"Native capture via mss on monitor {monitor_index} ({monitor['width']}x{monitor['height']})")
                sct_img = sct.grab(monitor)
                # Convert to PIL Image (mss returns BGRA)
                img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                return img
