from enum import Enum, auto


class InputAction(Enum):
    # Focus the question field, or capture with what it holds if it already
    # has focus. This is the hotkey's action.
    CAPTURE_SCREEN = auto()
    # Capture and generate a lesson for a question the learner has typed.
    # The text travels beside the action rather than inside it, since actions
    # cross threads as plain enum values.
    ASK = auto()
    # Open the area selector. Only prepares the region for the next ASK; no
    # capture happens until the learner explicitly asks.
    SELECT_REGION = auto()
    # Forget the prepared region so the next ASK captures the full screen.
    CLEAR_REGION = auto()
    TOGGLE_DEBUG = auto()
    # Drop whatever is running, whether or not a question is being drafted.
    # This is what the companion's explicit dismissals and the developer
    # panel's "any key interrupts the demo" path send.
    CANCEL_LESSON = auto()
    # One physical Escape. What it means depends on whether a question is
    # being drafted -- abandon the draft, else cancel -- and only the
    # controller knows that, so it is routed as its own intent rather than
    # pre-translated into CANCEL_LESSON at the source.
    ESCAPE = auto()
    NEXT_STEP = auto()
    PREV_STEP = auto()
