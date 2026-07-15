import logging
from typing import Callable, List
from src.input.state_machine import TutorState
from src.input.events import InputAction

logger = logging.getLogger(__name__)

class InputManager:
    def __init__(self):
        self.current_state = TutorState.IDLE
        self.listeners: List[Callable[[InputAction], None]] = []
        
    def set_state(self, new_state: TutorState):
        if self.current_state != new_state:
            logger.info(f"TutorState transition: {self.current_state.name} -> {new_state.name}")
            self.current_state = new_state
        
    def add_listener(self, callback: Callable[[InputAction], None]):
        self.listeners.append(callback)
        
    def handle_action(self, action: InputAction):
        logger.info(f"InputManager received action: {action.name} (Current State: {self.current_state.name})")
        
        if action == InputAction.CAPTURE_SCREEN:
            if self.current_state == TutorState.IDLE:
                self._dispatch(action)
            else:
                logger.warning(f"Ignored CAPTURE_SCREEN: Tutor is busy ({self.current_state.name})")
                # TODO: Implement "Cancel current lesson?" popup
                
        elif action == InputAction.CANCEL_LESSON:
            if self.current_state != TutorState.IDLE:
                self._dispatch(action)
                
        elif action == InputAction.TOGGLE_DEBUG:
            self._dispatch(action)
            
    def _dispatch(self, action: InputAction):
        for listener in self.listeners:
            listener(action)
