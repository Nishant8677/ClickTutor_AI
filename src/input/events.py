from enum import Enum, auto


class InputAction(Enum):
    # Focus the question field, or capture with what it holds if it already
    # has focus. This is the hotkey's action.
    CAPTURE_SCREEN = auto()
    # Capture and generate a lesson for a question the learner has typed.
    # The text travels beside the action rather than inside it, since actions
    # cross threads as plain enum values.
    ASK = auto()
    TOGGLE_DEBUG = auto()
    CANCEL_LESSON = auto()
    NEXT_STEP = auto()
    PREV_STEP = auto()
