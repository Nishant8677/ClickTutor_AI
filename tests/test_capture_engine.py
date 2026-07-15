from src.desktop.capture import CaptureEngine

engine = CaptureEngine()
try:
    path = engine.capture(target="screen")
    print(f"Success: {path}")
except Exception as e:
    print(f"Error: {e}")
