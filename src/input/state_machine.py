from enum import Enum, auto


class TutorState(Enum):
    IDLE = auto()
    # The area selector is on screen. Nothing else may capture, ask, navigate
    # or toggle debug until the drag finishes or is cancelled.
    SELECTING = auto()
    CAPTURING = auto()
    ANALYZING = auto()
    TEACHING = auto()
    FINISHED = auto()
