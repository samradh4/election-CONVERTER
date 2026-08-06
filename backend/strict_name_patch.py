from __future__ import annotations

import os
import re
from typing import List

import cv2
import pytesseract

from . import layout
from .models import PageResult, VoterRecord
from .utils import normalize_text

_APPLIED = False

# Several pages run concurrently. Restrict each Tesseract process to one OpenMP
# thread so four page workers do not multiply into dozens of CPU-heavy threads.
os.environ.setdefault("OMP_THREAD_LIMIT", "1")


def _needs_repair(record: VoterRecord) -> bool:
    body_fields = (
        record.name,
        record.relation,
        record.related_person_name,
        record.house_number,
        record.age,
        record.gender,
    )
    if not all(body_fields):
        return True
    return bool(
        re.search(r"[A-Za-z]", record.name or "")
        or re.search(r"[A-Za-z]", record.related_person_name or "")
    )


def _read_hindi_body(card_image) -> str:
    if card_image.size == 0:
        return ""
    height, width = card_image.shape[:2]
    crop = card_image[
        max(0, round(height * 0.05)) : max(1, round(height * 0.94)),
        : max(1, round(width * 0.77)),
    ]
    if crop.size == 0:
        return ""
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    gray = cv2.resize(gray, None, fx=1.8, fy=1.8, interpolation=cv2.INTER_CUBIC)
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    return normalize_text(
        pytesseract.image_to_string(
            gray,
            lang="hin",
            config="--oem 1 --psm 6 -c preserve_interword_spaces=1",
        )
    )


def apply() -> None:
    global _APPLIED
    if _APPLIED:
        return
    _APPLIED = True

    from . import strict_reader

    original_process_page = strict_reader._process_page

    def process_page_with_hindi_repair(
        pdf_path: str,
        page_index: int,
        config,
    ) -> PageResult:
        result = original_process_page(pdf_path, page_index, config)
        repair_indexes: List[int] = [
            index for index, record in enumerate(result.records) if _needs_repair(record)
        ]
        if not repair_indexes or not result.voter_page:
            return result

        image, _, _ = strict_reader.render_page(
            pdf_path,
            page_index,
            config.settings.dpi,
        )
        boxes, _, voter_page = layout.detect_voter_boxes(image, [], "")
        if not voter_page or len(boxes) < len(result.records):
            result.warnings.append(
                "पृष्ठ {}: Hindi repair के लिए card grid दोबारा नहीं मिला।".format(
                    page_index + 1
                )
            )
            return result

        for record_index in repair_indexes:
            if record_index >= len(boxes):
                continue
            record = result.records[record_index]
            card_image = layout.crop_box(image, boxes[record_index], padding=3)
            hindi_text = _read_hindi_body(card_image)
            if not hindi_text:
                continue
            retry = strict_reader._record_from_text(
                hindi_text,
                page_index + 1,
                record.source_card,
                result.metadata,
                record.photo_available == "हाँ",
                confidence=0.90,
            )
            merged = strict_reader.choose_better_record(record, retry)
            # Dedicated passes are authoritative for these two identifiers.
            merged.epic_id = record.epic_id
            merged.serial_number = record.serial_number
            strict_reader._finalize_record(merged)
            result.records[record_index] = merged

        return result

    strict_reader._process_page = process_page_with_hindi_repair
