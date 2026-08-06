from __future__ import annotations

import copy
import re
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from .models import VoterRecord
from .utils import clean_field, normalize_text

_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_EPIC_DIGIT_FIX = str.maketrans({"O": "0", "Q": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "B": "8", "G": "6"})
_VALID_EPIC = re.compile(r"^[A-Z]{3}[0-9]{7}$")
_APPLIED = False


def _ascii_digits(value: str) -> str:
    return (value or "").translate(_DEVANAGARI_DIGITS)


def valid_epic(value: str) -> bool:
    return bool(_VALID_EPIC.fullmatch((value or "").strip().upper()))


def extract_epic(text: str) -> str:
    value = normalize_text(text or "").translate(_DEVANAGARI_DIGITS).upper()
    compact = re.sub(r"[\s\-_/.:]", "", value)
    for token in re.findall(r"[A-Z0-9]{9,12}", compact):
        for start in range(max(1, len(token) - 9)):
            candidate = token[start:]
            if len(candidate) < 10:
                continue
            prefix = candidate[:3]
            if not prefix.isalpha():
                continue
            suffix = candidate[3:]
            if len(suffix) == 8 and suffix[0] in "OQ" and suffix[1:].isdigit():
                suffix = suffix[1:]
            suffix = suffix.translate(_EPIC_DIGIT_FIX)
            digits = re.sub(r"[^0-9]", "", suffix)
            if len(digits) == 7:
                return prefix + digits
    return ""


def extract_serial(lines: List[str]) -> str:
    candidates: List[int] = []
    for raw in lines[:7]:
        line = _ascii_digits(clean_field(raw, 120))
        if re.search(r"(?:आयु|उम्र|मकान|घर|AGE|HOUSE)", line, flags=re.IGNORECASE):
            continue
        match = re.match(r"^\s*[#|\[\](){}/\\\-]*\s*([0-9]{1,5})\s*[#|\[\](){}/\\\-]*\s*$", line)
        if match:
            candidates.append(int(match.group(1)))
            continue
        match = re.match(
            r"^\s*([0-9]{1,5})\s+(?:[A-Z]{3})[A-Z0-9\s\-_/.]{6,12}",
            line,
            flags=re.IGNORECASE,
        )
        if match:
            candidates.append(int(match.group(1)))
            continue
        match = re.match(
            r"^\s*(?:[A-Z]{3})[A-Z0-9\s\-_/.]{6,12}\s+([0-9]{1,5})\s*$",
            line,
            flags=re.IGNORECASE,
        )
        if match:
            candidates.append(int(match.group(1)))
            continue
        numbers = [int(value) for value in re.findall(r"(?<![0-9])([0-9]{1,5})(?![0-9])", line)]
        if len(numbers) >= 2 and max(numbers) >= 10:
            candidates.append(max(numbers))
    return str(max(candidates)) if candidates else ""


def _field_quality(field: str, value: str) -> Tuple[int, int]:
    value = clean_field(value, 120)
    if not value:
        return (0, 0)
    if field == "epic_id":
        return (3 if valid_epic(value) else 0, len(value))
    if field == "serial_number":
        return (2 if _ascii_digits(value).isdigit() else 0, len(value))
    if field == "age":
        try:
            age = int(_ascii_digits(value))
        except ValueError:
            return (0, 0)
        return (2 if 18 <= age <= 120 else 0, len(value))
    if field == "gender":
        return (2 if value in ("पुरुष", "महिला", "अन्य") else 1, len(value))
    if field in ("name", "related_person_name"):
        hindi = len(re.findall(r"[\u0900-\u097F]", value))
        latin_noise = len(re.findall(r"[A-Za-z]", value))
        return (2 + min(hindi, 20) - min(latin_noise, 5), len(value))
    return (1, len(value))


def merge_records(first: VoterRecord, second: VoterRecord) -> VoterRecord:
    primary = first if first.confidence >= second.confidence else second
    result = copy.deepcopy(primary)
    for field in (
        "serial_number",
        "epic_id",
        "name",
        "relation",
        "related_person_name",
        "house_number",
        "age",
        "gender",
        "photo_available",
        "constituency",
        "section",
        "part_number",
    ):
        first_value = getattr(first, field)
        second_value = getattr(second, field)
        chosen = first_value if _field_quality(field, first_value) >= _field_quality(field, second_value) else second_value
        setattr(result, field, chosen)

    raw_parts = [part for part in (first.raw_text, second.raw_text) if part]
    result.raw_text = "\n--- RETRY ---\n".join(dict.fromkeys(raw_parts))
    result.confidence = max(first.confidence, second.confidence)
    reasons: List[str] = []
    if not result.serial_number:
        reasons.append("क्रम संख्या गायब")
    if not valid_epic(result.epic_id):
        result.epic_id = ""
        reasons.append("EPIC गायब/अमान्य")
    if not result.name:
        reasons.append("नाम गायब")
    if not result.related_person_name:
        reasons.append("संबंधित व्यक्ति का नाम गायब")
    if not result.house_number:
        reasons.append("मकान संख्या गायब")
    if not result.age:
        reasons.append("आयु गायब/अमान्य")
    if not result.gender:
        reasons.append("लिंग गायब")
    result.review_required = bool(reasons)
    result.review_reason = "; ".join(reasons)
    return result


def _number(value: str) -> Optional[int]:
    text = _ascii_digits(value)
    return int(text) if text.isdigit() and 0 < int(text) < 1000000 else None


def reconcile_serials(records: List[VoterRecord]) -> None:
    if not records:
        return
    offsets: Counter[int] = Counter()
    anchors = 0
    for index, record in enumerate(records):
        number = _number(record.serial_number)
        if number is None:
            continue
        anchors += 1
        offsets[number - index] += 1
    if offsets:
        offset, support = offsets.most_common(1)[0]
        strong = support >= max(12, round(len(records) * 0.05)) and support >= max(4, round(anchors * 0.18))
        if strong:
            for index, record in enumerate(records):
                expected = index + offset
                if expected > 0 and _number(record.serial_number) != expected:
                    record.serial_number = str(expected)
                    record.serial_inferred = True
            return

    by_page: Dict[int, List[VoterRecord]] = defaultdict(list)
    for record in records:
        by_page[record.source_page].append(record)
    for page_records in by_page.values():
        page_records.sort(key=lambda item: item.source_card)
        page_offsets: Counter[int] = Counter()
        for record in page_records:
            number = _number(record.serial_number)
            if number is not None:
                page_offsets[number - record.source_card] += 1
        if not page_offsets:
            continue
        offset, support = page_offsets.most_common(1)[0]
        if support < max(3, round(len(page_records) * 0.20)):
            continue
        for record in page_records:
            expected = record.source_card + offset
            if expected > 0 and _number(record.serial_number) != expected:
                record.serial_number = str(expected)
                record.serial_inferred = True


def needs_card_retry(record: VoterRecord) -> bool:
    return any(
        not value
        for value in (
            record.serial_number,
            record.epic_id,
            record.name,
            record.relation,
            record.related_person_name,
            record.house_number,
            record.age,
            record.gender,
        )
    ) or not valid_epic(record.epic_id)


def _contour_boxes_fixed(image: np.ndarray):
    from . import layout

    height, width = image.shape[:2]
    _, horizontal, vertical = layout._grid_masks(image)
    grid = cv2.bitwise_or(horizontal, vertical)
    grid = cv2.dilate(grid, np.ones((3, 3), np.uint8), iterations=1)
    contours, _ = cv2.findContours(grid, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for contour in contours:
        x, y, box_width, box_height = cv2.boundingRect(contour)
        wr = box_width / float(width)
        hr = box_height / float(height)
        if not (0.23 <= wr <= 0.37 and 0.045 <= hr <= 0.14):
            continue
        if y < height * 0.02 or y + box_height > height * 0.98:
            continue
        boxes.append((x, y, box_width, box_height))
    return layout._dedupe_boxes(boxes)


def _projection_boxes_fixed(image: np.ndarray):
    from . import layout

    height, width = image.shape[:2]
    _, horizontal, vertical = layout._grid_masks(image)
    vertical_strength = np.count_nonzero(vertical, axis=0)
    horizontal_strength = np.count_nonzero(horizontal, axis=1)
    xs = layout.cluster_positions(np.where(vertical_strength > height * 0.19)[0], max(3, width // 600))
    ys = layout.cluster_positions(np.where(horizontal_strength > width * 0.48)[0], max(3, height // 900))
    verticals = layout._choose_verticals(xs, width)
    if len(verticals) != 4:
        return []
    intervals = []
    for top, bottom in zip(ys, ys[1:]):
        gap = bottom - top
        if height * 0.045 <= gap <= height * 0.14 and top > height * 0.02:
            intervals.append((top, bottom))
    return [
        (left, top, right - left, bottom - top)
        for top, bottom in intervals
        for left, right in zip(verticals, verticals[1:])
    ]


def apply() -> None:
    global _APPLIED
    if _APPLIED:
        return
    _APPLIED = True

    from . import parser

    parser._extract_serial = extract_serial
    parser._extract_epic = extract_epic
    parser.choose_better_record = merge_records
    parser.reconcile_serials = reconcile_serials
    parser.AGE_RE = re.compile(r"(?:आयु|उम्र|AGE)\s*[:\-]?\s*([0-9०-९]{1,3})", re.IGNORECASE)

    from . import layout

    layout._contour_boxes = _contour_boxes_fixed
    layout._projection_boxes = _projection_boxes_fixed

    from . import converter

    converter._needs_hybrid_card_retry = needs_card_retry
    converter.choose_better_record = merge_records
    converter.reconcile_serials = reconcile_serials

    original_convert_pdf = converter.convert_pdf
    from .strict_reader import convert_pdf_strict

    def routed_convert_pdf(pdf_path, config=None, progress_callback=None):
        mode = getattr(config, "mode", "turbo") if config is not None else "turbo"
        if mode == "turbo":
            return convert_pdf_strict(pdf_path, config=config, progress_callback=progress_callback)
        return original_convert_pdf(pdf_path, config=config, progress_callback=progress_callback)

    converter.convert_pdf = routed_convert_pdf
