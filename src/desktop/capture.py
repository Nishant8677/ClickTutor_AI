import os
import subprocess
import mss

class CaptureEngine:
    def __init__(self, temp_dir=".temp"):
        self.temp_dir = temp_dir
        if not os.path.exists(self.temp_dir):
            os.makedirs(self.temp_dir)
            
    def _is_wsl(self):
        return "WSL" in os.uname().release or os.path.exists("/run/WSL")

    def capture(self, target="screen"):
        """
        Captures the specified target and returns the file path to the saved image.
        For now, only target="screen" is supported, which captures the primary monitor.
        """
        output_path = os.path.abspath(os.path.join(self.temp_dir, "current_capture.png"))
        
        if target == "screen":
            if self._is_wsl():
                # WSLg X11 cannot capture the Windows desktop (XGetImage fails)
                # Fallback to invoking PowerShell natively on Windows
                script_path = os.path.join(os.path.dirname(__file__), "capture.ps1")
                # Convert paths to Windows format
                win_output_path = subprocess.check_output(["wslpath", "-w", output_path]).decode().strip()
                win_script_path = subprocess.check_output(["wslpath", "-w", script_path]).decode().strip()
                
                subprocess.run([
                    "powershell.exe", "-ExecutionPolicy", "Bypass",
                    "-File", win_script_path, win_output_path
                ], check=True)
                return output_path
            else:
                with mss.mss() as sct:
                    # monitor 1 is usually the primary monitor
                    sct.shot(mon=1, output=output_path)
                    return output_path
        else:
            raise NotImplementedError(f"Capture target '{target}' not yet supported.")
