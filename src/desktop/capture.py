import os
import mss

class CaptureEngine:
    def __init__(self, temp_dir=".temp"):
        self.temp_dir = temp_dir
        if not os.path.exists(self.temp_dir):
            os.makedirs(self.temp_dir)
            
    def capture(self, target="screen"):
        """
        Captures the specified target and returns the file path to the saved image.
        For now, only target="screen" is supported, which captures the primary monitor.
        """
        output_path = os.path.join(self.temp_dir, "current_capture.png")
        
        if target == "screen":
            with mss.mss() as sct:
                # monitor 1 is usually the primary monitor
                sct.shot(mon=1, output=output_path)
                return output_path
        else:
            raise NotImplementedError(f"Capture target '{target}' not yet supported.")
