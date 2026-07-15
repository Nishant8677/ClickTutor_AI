from enum import Enum, auto

class TutorState(Enum):
    IDLE = auto()
    CAPTURING = auto()
    ANALYZING = auto()
    TEACHING = auto()
    FINISHED = auto()
