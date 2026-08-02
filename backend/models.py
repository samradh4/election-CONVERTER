from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class OCRWord:
    text: str
    left: int
    top: int
    right: int
    bottom: int
    confidence: float = 0.0
    line_key: Optional[Tuple[int, ...]] = None

    @property
    def center(self) -> Tuple[float, float]:
        return ((self.left + self.right) / 2.0, (self.top + self.bottom) / 2.0)


@dataclass
class VoterRecord:
    serial_number: str = ""
    epic_id: str = ""
    name: str = ""
    relation: str = ""
    related_person_name: str = ""
    house_number: str = ""
    age: str = ""
    gender: str = ""
    photo_available: str = ""
    constituency: str = ""
    section: str = ""
    part_number: str = ""
    source_page: int = 0
    source_card: int = 0
    confidence: float = 0.0
    review_required: bool = False
    review_reason: str = ""
    raw_text: str = ""
    duplicate_epic: bool = False
    serial_inferred: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PageResult:
    page_number: int
    records: List[VoterRecord] = field(default_factory=list)
    metadata: Dict[str, str] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    used_text_layer: bool = False
    detected_boxes: int = 0
    layout_method: str = ""
    voter_page: bool = False


@dataclass
class ConversionResult:
    records: List[VoterRecord]
    metadata: Dict[str, str]
    page_count: int
    warnings: List[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    review_count: int = 0
    detected_card_count: int = 0
