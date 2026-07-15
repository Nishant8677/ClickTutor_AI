import os
import logging
import numpy as np
import imageio
from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QPixmap, QImage

logger = logging.getLogger(__name__)

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

        # Handle QImage 32-bit row alignment padding using strides
        frame = np.ndarray(
            shape=(height, width, 3),
            dtype=np.uint8,
            buffer=arr,
            offset=0,
            strides=(image.bytesPerLine(), 3, 1)
        ).copy()  # copy to make it contiguous
        
        # Ensure dimensions are even for H264 encoding
        if height % 2 != 0 or width % 2 != 0:
            frame = frame[:height - (height % 2), :width - (width % 2), :]
            
        self.frames.append(frame)

    def _save_mp4(self, output_path):
        if not self.frames:
            return

        try:
            writer = imageio.get_writer(output_path, fps=self.fps, macro_block_size=None)
            for frame in self.frames:
                writer.append_data(frame)
            writer.close()
        except Exception as e:
            logger.error("Failed to save MP4 to '%s': %s", output_path, e)
