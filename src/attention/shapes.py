from dataclasses import dataclass, field
from enum import Enum, auto

class AnimationType(Enum):
    NONE = auto()
    FADE = auto()
    PULSE = auto()
    DRAW = auto()
    BOUNCE = auto()
    SHAKE = auto()

@dataclass
class AttentionShape:
    """Base class for all attention shapes"""
    x: int
    y: int
    width: int
    height: int
    
    # Animation properties
    opacity: float = 0.0
    scale: float = 1.0
    glow_strength: float = 0.0
    animation_types: list[AnimationType] = field(default_factory=lambda: [AnimationType.FADE, AnimationType.PULSE])

@dataclass
class RectangleShape(AttentionShape):
    padding: int = 6
    outline_color: str = "red"
    outline_width: int = 4

@dataclass
class CircleShape(AttentionShape):
    padding: int = 10
    outline_color: str = "blue"
    outline_width: int = 4

@dataclass
class UnderlineShape(AttentionShape):
    padding: int = 2
    outline_color: str = "green"
    outline_width: int = 4

@dataclass
class LabelShape(AttentionShape):
    text: str
    bg_color: str = "yellow"
    text_color: str = "black"
    
    def __post_init__(self):
        # Labels usually just fade, no pulse needed by default
        self.animation_types = [AnimationType.FADE]

@dataclass
class DebugBoxShape(AttentionShape):
    text: str
    confidence: float
    
    def __post_init__(self):
        # Debug boxes shouldn't animate
        self.animation_types = [AnimationType.NONE]
        self.opacity = 1.0

