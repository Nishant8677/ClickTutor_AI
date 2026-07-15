import logging
from src.input.events import InputAction

logger = logging.getLogger(__name__)

class HotkeyManager:
    def __init__(self, callback):
        """
        callback: function that accepts an InputAction
        """
        self.callback = callback
    
    def start(self):
        logger.info("HotkeyManager started (stub)")
        
    def stop(self):
        logger.info("HotkeyManager stopped (stub)")
