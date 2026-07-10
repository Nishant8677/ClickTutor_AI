import os
import imageio
from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QPixmap, QImage

class Mp4Recorder(QObject):
    recording_finished = pyqtSignal(str) # Emits the output path when done

    def __init__(self, overlay, fps=15, output_dir="."):
        super().__init__()
        self.overlay = overlay
        self.fps = fps
        self.interval_ms = int(1000 / fps)
        self.output_dir = output_dir
        self.frames = []
        
        self.timer = QTimer()
        self.timer.timeout.connect(self._capture_frame)
        self.is_recording = False

    def start_recording(self):
        self.frames = []
        self.is_recording = True
        self.timer.start(self.interval_ms)

    def stop_recording(self, output_filename="demo_output.mp4"):
        if not self.is_recording:
            return
            
        self.is_recording = False
        self.timer.stop()
        
        output_path = os.path.join(self.output_dir, output_filename)
        self._save_mp4(output_path)
        self.recording_finished.emit(output_path)

    def _capture_frame(self):
        # Grab the entire overlay widget (which includes the background and shapes)
        pixmap = self.overlay.grab()
        image = pixmap.toImage()
        
        # Convert QImage to RGB888 bytes
        image = image.convertToFormat(QImage.Format.Format_RGB888)
        width = image.width()
        height = image.height()
        
        # ptr is a memory view, we need to extract bytes
        ptr = image.bits()
        ptr.setsize(image.sizeInBytes())
        arr = bytearray(ptr)
        
        import numpy as np
        frame = np.array(arr).reshape((height, width, 3))
        self.frames.append(frame)

    def _save_mp4(self, output_path):
        if not self.frames:
            print("No frames captured.")
            return
            
        print(f"Saving MP4 ({len(self.frames)} frames) to {output_path}...")
        try:
            # We use macro_block_size=None or 1 if dimensions aren't divisible by 16, 
            # but imageio usually handles it.
            writer = imageio.get_writer(output_path, fps=self.fps, macro_block_size=None)
            for frame in self.frames:
                writer.append_data(frame)
            writer.close()
            print("MP4 saved successfully!")
        except Exception as e:
            print(f"Failed to save MP4: {e}")
