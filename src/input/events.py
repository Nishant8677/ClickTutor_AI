from enum import Enum, auto


class InputAction(Enum):
    CAPTURE_SCREEN = auto()
    TOGGLE_DEBUG = auto()
    CANCEL_LESSON = auto()
    NEXT_STEP = auto()
    PREV_STEP = auto()
