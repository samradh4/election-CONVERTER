from __future__ import annotations

import copy
import re
from collections import Counter
from typing import List

from .models import VoterRecord
from .utils import clean_field, normalize_text

_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_OCR_DIGITS = str.maketrans({"O": "0", "Q": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "B": "8", "G": "6"})


def _ascii_digits(value: str) -> str:
    return (value or "").translate(_DEVANAGARI_DIGITS)


def extract_serial(lines: List[str]) -> str:
    """Read the roll serial from the top of a voter card without mistaking age/house values."""
    normalized = [_ascii_digits(clean_field(line, 120)) for line in lines[:7]]
    for line in normalized:
        match = re.match(r"^\s*([0-9]{1,6})\s*(?:[.)\-:]\s*)?$", line)
        if match:
            return str(int(match.group(1)))

        # Common card header: serial and EPIC on the same OCR line.
        match = re.match(
            r"^\s*([0-9]{1,6})\s+(?:[A-Z]{2,4})[\s\-_/]*[0-9OQILZSBG]{6,10}\b",
            line,
            flags=re.IGNORECASE,
        )
        if match:
            return str(int(match.group(1)))

    return ""


def extract_epic(text: str) -> str:
    """Tolerant EPIC reader for spacing and OCR digit confusions."""
    value = _ascii_digits(normalize_text(text)).upper()
    patterns = (
        r"(?<![A-Z0-9])([A-Z]{2,4})[\s\-_/.:]*([0-9OQILZSBG\s\-_/]{6,16})(?![A-Z])",
        r"\b(?:EPIC|पहचान|निर्वाचक)[^A-Z0-9]{0,15}([A-Z]{2,4})[\s\-_/.:]*([0-9OQILZSBG\s\-_/]{6,16})",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, value, flags=re.IGNORECASE):
            prefix = match.group(1).upper()
            raw = re.sub(r"[\s\-_/.:]", "", match.group(2).upper())
            actual_digits = sum(char.isdigit() for char in raw)
            digits = raw.translate(_OCR_DIGITS)
            digits = re.sub(r"[^0-9]", "", digits)
            if 6 <= len(digits) <= 10 and actual_digits >= 5:
                return prefix + digits
    return ""


def merge_records(first: VoterRecord, second: VoterRecord) -> VoterRecord:
    """Combine the page OCR and card OCR field-by-field instead of discarding useful values."""
    first_present = sum(bool(getattr(first, field)) for field in (
        "serial_number", "epic_id", "name", "related_person_name", "house_number", "age", "gender"
    ))
    second_present = sum(bool(getattr(second, field)) for field in (
        "serial_number", "epic_id", "name", "related_person_name", "house_number", "age", "gender"
    ))
    primary, secondary = (second, first) if (second_present, second.confidence) > (first_present, first.confidence) else (first, second)
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
        if not getattr(result, field) and getattr(secondary, field):
            setattr(result, field, getattr(secondary, field))

    raw_parts = [part for part in (first.raw_text, second.raw_text) if part]
    result.raw_text = "\n--- CARD RETRY ---\n".join(dict.fromkeys(raw_parts))
    result.confidence = max(first.confidence, second.confidence)

    reasons = []
    if not result.name:
        reasons.append("नाम गायब")
    if not result.epic_id:
        reasons.append("EPIC गायब")
    if not result.age:
        reasons.append("आयु गायब/अमान्य")
    if not result.gender:
        reasons.append("लिंग गायब")
    if not result.serial_number:
        reasons.append("क्रम संख्या OCR से नहीं मिली")
    result.review_required = bool(reasons)
    result.review_reason = "; ".join(reasons)
    return result


def reconcile_serials(records: List[VoterRecord]) -> None:
    """Fill sequence gaps only where existing serial anchors prove the sequence."""
    if not records:
        return

    anchors = []
    for index, record in enumerate(records):
        value = _ascii_digits(record.serial_number)
        if value.isdigit():
            number = int(value)
            if 1 <= number <= 999999:
                record.serial_number = str(number)
                anchors.append((index, number))

    # First fill gaps bounded by two exact sequential anchors.
    for (left_i, left_n), (right_i, right_n) in zip(anchors, anchors[1:]):
        if right_i <= left_i + 1:
            continue
        if right_n - left_n != right_i - left_i:
            continue
        for index in range(left_i + 1, right_i):
            if not records[index].serial_number:
                _set_inferred(records[index], left_n + (index - left_i))

    # Two or more anchors with the same offset safely establish the whole sequence.
    offsets = Counter(number - index for index, number in anchors)
    if not offsets:
        return
    offset, support = offsets.most_common(1)[0]
    if support < 2:
        return

    for index, record in enumerate(records):
        expected = index + offset
        if expected <= 0:
            continue
        current = int(record.serial_number) if record.serial_number.isdigit() else None
        if current is None:
            _set_inferred(record, expected)
        elif abs(current - expected) > 1:
            old = record.serial_number
            _set_inferred(record, expected, old)


def _set_inferred(record: VoterRecord, value: int, old: str = "") -> None:
    record.serial_number = str(value)
    record.serial_inferred = True
    record.review_required = True
    note = "क्रम संख्या प्रमाणित क्रम के आधार पर भरी गई"
    if old:
        note += " (OCR: {})".format(old)
    record.review_reason = (record.review_reason + "; " if record.review_reason else "") + note


def needs_card_retry(record: VoterRecord) -> bool:
    """Retry a card when any client-required field is missing."""
    required_missing = any(
        not value
        for value in (
            record.serial_number,
            record.epic_id,
            record.name,
            record.age,
            record.gender,
        )
    )
    supporting_missing = sum(
        not value
        for value in (
            record.related_person_name,
            record.house_number,
        )
    ) >= 1
    return required_missing or supporting_missing


def apply() -> None:
    from . import parser

    parser._extract_serial = extract_serial
    parser._extract_epic = extract_epic
    parser.choose_better_record = merge_records
    parser.reconcile_serials = reconcile_serials

    # Accept both ASCII and Devanagari age digits. Python int() supports Unicode digits.
    parser.AGE_RE = re.compile(
        r"(?:आयु|उम्र|AGE)\s*[:\-]?\s*([0-9०-९]{1,3})",
        re.IGNORECASE,
    )

    from . import converter

    converter._needs_hybrid_card_retry = needs_card_retry
    converter.choose_better_record = merge_records
    converter.reconcile_serials = reconcile_serials
