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
    # A follow-up about the displayed step is being answered. The lesson and
    # its highlight stay on screen, but asking, capturing, selecting,
    # navigating and the debug overlay wait; Escape cancels the follow-up.
    ANSWERING = auto()
