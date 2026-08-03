import sys
from pathlib import Path

# Running this file directly puts tools/manual on sys.path, not the repo
# root, so "src" would not be importable without this.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.desktop.capture import CaptureEngine

engine = CaptureEngine()
try:
    path = engine.capture(target="screen")
    print(f"Success: {path}")
except Exception as e:
    print(f"Error: {e}")
