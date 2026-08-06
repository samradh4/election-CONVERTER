from __future__ import annotations

from typing import List

import cv2
import pytesseract

from . import layout
from .legacy_epic_patch import extract_epic, valid_epic
from .models import PageResult
from .utils import normalize_text

_APPLIED = False


def _read_epic_crop(card_image) -> str:
    if card_image.size == 0:
        return ""
    height, width = card_image.shape[:2]
    crop = card_image[
        : max(1, round(height * 0.30)),
        max(0, round(width * 0.38)) :,
    ]
    if crop.size == 0:
        return ""
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    gray = cv2.resize(gray, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)

    for psm in (7, 11):
        text = normalize_text(
            pytesseract.image_to_string(
                gray,
                lang="eng",
                config=(
                    "--oem 1 --psm {} "
                    "-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/"
                ).format(psm),
            )
        )
        epic = extract_epic(text)
        if valid_epic(epic):
            return epic
    return ""


def apply() -> None:
    global _APPLIED
    if _APPLIED:
        return
    _APPLIED = True

    from . import strict_reader

    original_process_page = strict_reader._process_page

    def process_page_with_epic_repair(pdf_path: str, page_index: int, config) -> PageResult:
        result = original_process_page(pdf_path, page_index, config)
        missing_indexes: List[int] = [
            index
            for index, record in enumerate(result.records)
            if not valid_epic(record.epic_id)
        ]
        if not missing_indexes or not result.voter_page:
            return result

        image, _, _ = strict_reader.render_page(
            pdf_path,
            page_index,
            config.settings.dpi,
        )
        boxes, _, voter_page = layout.detect_voter_boxes(image, [], "")
        if not voter_page or len(boxes) < len(result.records):
            result.warnings.append(
                "पृष्ठ {}: missing EPIC repair के लिए card grid दोबारा नहीं मिला।".format(
                    page_index + 1
                )
            )
            return result

        repaired = 0
        for record_index in missing_indexes:
            if record_index >= len(boxes):
                continue
            card_image = layout.crop_box(image, boxes[record_index], padding=3)
            epic = _read_epic_crop(card_image)
            if not epic:
                continue
            result.records[record_index].epic_id = epic
            strict_reader._finalize_record(result.records[record_index])
            repaired += 1

        if repaired:
            result.warnings.append(
                "पृष्ठ {}: {} EPIC targeted repair से ठीक हुए।".format(
                    page_index + 1, repaired
                )
            )
        return result

    strict_reader._process_page = process_page_with_epic_repair
