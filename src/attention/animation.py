import math
from PyQt6.QtCore import QObject, QTimer
from src.attention.shapes import AnimationType

class AnimationEngine(QObject):
    def __init__(self, update_callback):
        super().__init__()
        self.update_callback = update_callback
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.setInterval(16) # ~60 FPS
        
        self.shapes = []
        self.elapsed_ms = 0
        
        # Animation config
        self.fade_duration_ms = 300
        self.pause_duration_ms = 200
        self.pulse_cycles = 2.5 # End at peak or trough, let's say 2 full cycles.
        self.pulse_cycle_duration_ms = 800
        self.pulse_duration_ms = int(self.pulse_cycles * self.pulse_cycle_duration_ms)
        self.total_anim_duration = self.fade_duration_ms + self.pause_duration_ms + self.pulse_duration_ms

    def start(self, shapes):
        """Starts or restarts the animation sequence for a new set of shapes."""
        self.shapes = shapes
        self.elapsed_ms = 0
        
        # Initialize shapes to starting state
        for shape in self.shapes:
            if AnimationType.FADE in shape.animation_types:
                shape.opacity = 0.0
            else:
                shape.opacity = 1.0
                
            shape.glow_strength = 0.0
            shape.scale = 1.0
            
        self.update_callback()
        
        if not self.timer.isActive():
            self.timer.start()

    def stop(self):
        self.timer.stop()
        
    def _ease_out_cubic(self, t):
        return 1 - math.pow(1 - t, 3)

    def _tick(self):
        self.elapsed_ms += 16
        
        needs_update = False
        all_idle = True
        
        for shape in self.shapes:
            # 1. Fade-in phase
            if AnimationType.FADE in shape.animation_types:
                if self.elapsed_ms <= self.fade_duration_ms:
                    t = self.elapsed_ms / self.fade_duration_ms
                    shape.opacity = self._ease_out_cubic(t)
                    needs_update = True
                    all_idle = False
                elif shape.opacity < 1.0:
                    shape.opacity = 1.0
                    needs_update = True

            # 2. Pulse phase (Glow breathing)
            if AnimationType.PULSE in shape.animation_types:
                pulse_start = self.fade_duration_ms + self.pause_duration_ms
                pulse_end = pulse_start + self.pulse_duration_ms
                
                if pulse_start <= self.elapsed_ms <= pulse_end:
                    t_pulse = self.elapsed_ms - pulse_start
                    # Smooth breathing using inverted cosine
                    # 0 at start, 1 at peak, 0 at end of cycle
                    progress = t_pulse / self.pulse_cycle_duration_ms
                    shape.glow_strength = 0.5 * (1 - math.cos(progress * 2 * math.pi))
                    needs_update = True
                    all_idle = False
                elif self.elapsed_ms > pulse_end and shape.glow_strength > 0.0:
                    # Fade out glow cleanly at the end if it's not perfectly 0
                    shape.glow_strength = max(0.0, shape.glow_strength - 0.1)
                    needs_update = True
                    all_idle = False
                elif self.elapsed_ms < pulse_start:
                    all_idle = False

        if needs_update:
            self.update_callback()
            
        if all_idle and self.elapsed_ms > self.total_anim_duration:
            self.timer.stop()
