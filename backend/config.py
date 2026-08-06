from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def _resource_root() -> Path:
    """Read-only application files bundled by PyInstaller."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent.parent


def _data_root() -> Path:
    """Writable location for uploads and generated workbooks."""
    if getattr(sys, "frozen", False) and os.name == "nt":
        base = Path(os.getenv("LOCALAPPDATA", str(Path.home())))
        return base / "ElectionPDFConverter"
    return Path(__file__).resolve().parent.parent


RESOURCE_ROOT = _resource_root()
DATA_ROOT = _data_root()
UPLOAD_DIR = DATA_ROOT / "uploads"
OUTPUT_DIR = DATA_ROOT / "outputs"

_BUNDLED_TESSDATA = RESOURCE_ROOT / "tesseract" / "tessdata"
_PROJECT_TESSDATA = RESOURCE_ROOT / "tessdata"
TESSDATA_DIR = _BUNDLED_TESSDATA if _BUNDLED_TESSDATA.is_dir() else _PROJECT_TESSDATA


@dataclass(frozen=True)
class ModeSettings:
    dpi: int
    workers: int
    card_ocr_policy: str  # never, missing-fields, fallback, always
    min_record_confidence: float
    page_psm: int
    card_psm: int


def _workers(limit: int) -> int:
    cpu = os.cpu_count() or 4
    return max(1, min(limit, max(1, cpu - 1)))


MODES = {
    # Fastest option for clean scans. One OCR pass per page and no card-level retry.
    "fast": ModeSettings(
        dpi=180,
        workers=_workers(6),
        card_ocr_policy="never",
        min_record_confidence=0.58,
        page_psm=11,
        card_psm=6,
    ),
    # Default one-click mode: use embedded PDF text instantly when present,
    # otherwise OCR each scanned page once and retry only cards missing key fields.
    "hybrid": ModeSettings(
        dpi=220,
        workers=_workers(6),
        card_ocr_policy="missing-fields",
        min_record_confidence=0.64,
        page_psm=11,
        card_psm=6,
    ),
    "balanced": ModeSettings(
        dpi=250,
        workers=_workers(5),
        card_ocr_policy="fallback",
        min_record_confidence=0.70,
        page_psm=11,
        card_psm=6,
    ),
    "accurate": ModeSettings(
        dpi=320,
        workers=_workers(3),
        card_ocr_policy="always",
        min_record_confidence=0.78,
        page_psm=11,
        card_psm=6,
    ),
}


@dataclass
class ConversionConfig:
    mode: str = "hybrid"
    languages: str = "hin+eng"
    constituency_override: str = ""
    section_override: str = ""
    part_override: str = ""
    use_manual_metadata: bool = False
    max_pages: Optional[int] = None

    @property
    def settings(self) -> ModeSettings:
        return MODES.get(self.mode, MODES["hybrid"])
