from dataclasses import dataclass

@dataclass
class AttentionShape:
    """Base class for all attention shapes"""
    x: int
    y: int
    width: int
    height: int

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

@dataclass
class DebugBoxShape(AttentionShape):
    text: str
    confidence: float
