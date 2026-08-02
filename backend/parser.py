from __future__ import annotations

import re
from collections import Counter
from typing import Dict, Iterable, List, Optional, Tuple

from .models import VoterRecord
from .utils import clean_field, most_common_nonempty, normalize_text

EPIC_RE = re.compile(r"\b([A-Z]{2,4})[\s\-_/]*([0-9]{6,10})\b", re.IGNORECASE)
AGE_RE = re.compile(r"(?:आयु|उम्र|AGE)\s*[:\-]?\s*([0-9]{1,3})", re.IGNORECASE)
GENDER_RE = re.compile(
    r"(?:लिंग|SEX|GENDER)\s*[:\-]?\s*(पुरुष|महिला|तृतीय\s*लिंग|अन्य|MALE|FEMALE|OTHER)",
    re.IGNORECASE,
)
HOUSE_RE = re.compile(
    r"(?:मकान\s*(?:संख्या|नं\.?|नम्बर)?|घर\s*(?:संख्या|नं\.?)?|HOUSE\s*(?:NO\.?|NUMBER)?)\s*[:\-]?\s*([^\n]{1,36})",
    re.IGNORECASE,
)
NAME_RE = re.compile(r"(?:^|\n)\s*(?:नाम|NAME)\s*[:\-]?\s*([^\n]{1,55})", re.IGNORECASE)
RELATION_RE = re.compile(
    r"(?:^|\n)\s*(पिता|पति|माता|अन्य|FATHER|HUSBAND|MOTHER|GUARDIAN)\s*(?:का\s*नाम|NAME)?\s*[:\-]?\s*([^\n]{1,55})",
    re.IGNORECASE,
)
SERIAL_RE = re.compile(r"^\s*([0-9]{1,5})\s*(?:\.|\)|\-)?\s*$")

STOP_LABELS = re.compile(
    r"\s+(?:पिता|पति|माता|अन्य|FATHER|HUSBAND|MOTHER|GUARDIAN|मकान|घर|HOUSE|आयु|उम्र|AGE|लिंग|SEX|GENDER)\b.*$",
    re.IGNORECASE,
)


def _clean_name(value: str) -> str:
    value = STOP_LABELS.sub("", value)
    value = re.sub(r"\b(?:फोटो|PHOTO)\b.*$", "", value, flags=re.IGNORECASE)
    return clean_field(value, 55)


def _map_gender(value: str) -> str:
    value = clean_field(value).lower()
    if value in ("male", "पुरुष"):
        return "पुरुष"
    if value in ("female", "महिला"):
        return "महिला"
    if value:
        return "अन्य"
    return ""


def _map_relation(value: str) -> str:
    value = clean_field(value).lower()
    mapping = {
        "father": "पिता",
        "पिता": "पिता",
        "husband": "पति",
        "पति": "पति",
        "mother": "माता",
        "माता": "माता",
        "guardian": "अन्य",
        "अन्य": "अन्य",
    }
    return mapping.get(value, clean_field(value))


def _value_after_label(lines: List[str], labels: List[str]) -> str:
    for index, line in enumerate(lines):
        for label in labels:
            match = re.search(label, line, flags=re.IGNORECASE)
            if not match:
                continue
            trailing = clean_field(line[match.end() :], 80)
            trailing = re.sub(r"^[\s:;,.\-–—]+", "", trailing)
            trailing = re.split(
                r"\s+(?:विधानसभा|अनुभाग|भाग|कुल|ASSEMBLY|SECTION|PART|TOTAL)\b",
                trailing,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0]
            if trailing and not re.search(
                r"^(?:विधानसभा|अनुभाग|भाग|ASSEMBLY|SECTION|PART)$",
                trailing,
                flags=re.IGNORECASE,
            ):
                return clean_field(trailing, 80)
            for candidate in lines[index + 1 : index + 4]:
                if re.search(
                    r"(?:विधानसभा|अनुभाग|भाग|ASSEMBLY|SECTION|PART)",
                    candidate,
                    flags=re.IGNORECASE,
                ):
                    continue
                candidate = clean_field(candidate, 80)
                if candidate:
                    return candidate
    return ""


def parse_metadata(text: str) -> Dict[str, str]:
    text = normalize_text(text)
    lines = [clean_field(line, 100) for line in text.splitlines() if clean_field(line, 100)]
    metadata = {}

    constituency = _value_after_label(
        lines,
        [
            r"विधानसभा\s*(?:निर्वाचन\s*)?क्षेत्र(?:\s*की\s*संख्या\s*और\s*नाम)?",
            r"ASSEMBLY\s*CONSTITUENCY",
        ],
    )
    section = _value_after_label(
        lines,
        [
            r"अनुभाग\s*(?:संख्या\s*और\s*नाम)?",
            r"SECTION\s*(?:NO\.?\s*AND\s*NAME)?",
        ],
    )
    part = _value_after_label(
        lines,
        [r"भाग\s*(?:संख्या|नं\.?)", r"PART\s*(?:NO\.?|NUMBER)"],
    )

    flat = re.sub(r"\s+", " ", text)
    if not constituency:
        match = re.search(
            r"(?:विधानसभा\s*(?:निर्वाचन\s*)?क्षेत्र[^:]{0,35}|ASSEMBLY\s*CONSTITUENCY)\s*[:\-]?\s*([0-9]{1,4}\s*[-–]\s*[^|]{2,50})",
            flat,
            flags=re.IGNORECASE,
        )
        if match:
            constituency = clean_field(match.group(1), 70)
    if not section:
        match = re.search(
            r"(?:अनुभाग[^:]{0,25}|SECTION[^:]{0,25})\s*[:\-]?\s*([0-9]{1,4}\s*[-–]\s*[^|]{1,50})",
            flat,
            flags=re.IGNORECASE,
        )
        if match:
            section = clean_field(match.group(1), 70)
    if part:
        match = re.search(r"[0-9]{1,5}", part)
        part = match.group(0) if match else ""
    if not part:
        match = re.search(
            r"(?:भाग\s*(?:संख्या|नं\.?)|PART\s*(?:NO\.?|NUMBER))\s*[:\-]?\s*([0-9]{1,5})",
            flat,
            flags=re.IGNORECASE,
        )
        if match:
            part = match.group(1)

    if constituency:
        metadata["constituency"] = clean_field(constituency, 70)
    if section:
        metadata["section"] = clean_field(section, 70)
    if part:
        metadata["part_number"] = clean_field(part, 10)
    return metadata


def merge_metadata(
    metadata_items: Iterable[Dict[str, str]], overrides: Optional[Dict[str, str]] = None
) -> Dict[str, str]:
    items = list(metadata_items)
    result = {
        "constituency": most_common_nonempty(item.get("constituency", "") for item in items),
        "section": most_common_nonempty(item.get("section", "") for item in items),
        "part_number": most_common_nonempty(item.get("part_number", "") for item in items),
    }
    for key, value in (overrides or {}).items():
        if clean_field(value):
            result[key] = clean_field(value, 80)
    return result


def _extract_serial(lines: List[str]) -> str:
    for line in lines[:4]:
        match = SERIAL_RE.match(line)
        if match:
            return match.group(1)
        match = re.match(r"^\s*([0-9]{1,5})\s+[A-Z]{2,4}\d", line, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def _extract_epic(text: str) -> str:
    match = EPIC_RE.search(text.upper())
    if not match:
        return ""
    return (match.group(1) + match.group(2)).upper()


def _fallback_name(lines: List[str]) -> str:
    ignored = re.compile(
        r"(?:EPIC|आयु|AGE|लिंग|GENDER|SEX|मकान|HOUSE|पिता|पति|माता|FATHER|HUSBAND|MOTHER)",
        re.IGNORECASE,
    )
    for line in lines:
        if ignored.search(line):
            continue
        if SERIAL_RE.match(line) or EPIC_RE.search(line):
            continue
        if len(re.sub(r"[^A-Za-z\u0900-\u097F]", "", line)) >= 3:
            return _clean_name(line)
    return ""


def parse_record(
    text: str,
    page_number: int,
    card_number: int,
    metadata: Dict[str, str],
    ocr_confidence: float,
    photo_available: bool,
    min_confidence: float,
) -> VoterRecord:
    text = normalize_text(text)
    lines = [clean_field(line, 100) for line in text.splitlines() if clean_field(line, 100)]
    record = VoterRecord(
        source_page=page_number,
        source_card=card_number,
        raw_text=text,
        constituency=metadata.get("constituency", ""),
        section=metadata.get("section", ""),
        part_number=metadata.get("part_number", ""),
        photo_available="हाँ" if photo_available else "नहीं",
    )
    record.serial_number = _extract_serial(lines)
    record.epic_id = _extract_epic(text)

    match = NAME_RE.search("\n" + text)
    record.name = _clean_name(match.group(1)) if match else _fallback_name(lines)

    match = RELATION_RE.search("\n" + text)
    if match:
        record.relation = _map_relation(match.group(1))
        record.related_person_name = _clean_name(match.group(2))

    match = HOUSE_RE.search(text)
    if match:
        value = match.group(1)
        value = re.split(r"(?:आयु|उम्र|AGE|लिंग|SEX|GENDER)", value, maxsplit=1, flags=re.IGNORECASE)[0]
        record.house_number = clean_field(value, 30)

    match = AGE_RE.search(text)
    if match:
        age = int(match.group(1))
        if 18 <= age <= 120:
            record.age = str(age)

    match = GENDER_RE.search(text)
    if match:
        record.gender = _map_gender(match.group(1))

    completeness = {
        "serial": bool(record.serial_number),
        "epic": bool(record.epic_id),
        "name": bool(record.name),
        "relation_name": bool(record.related_person_name),
        "house": bool(record.house_number),
        "age": bool(record.age),
        "gender": bool(record.gender),
    }
    weights = {
        "serial": 0.08,
        "epic": 0.18,
        "name": 0.20,
        "relation_name": 0.14,
        "house": 0.10,
        "age": 0.15,
        "gender": 0.15,
    }
    field_score = sum(weights[key] for key, present in completeness.items() if present)
    record.confidence = max(0.0, min(1.0, field_score * 0.78 + ocr_confidence * 0.22))

    reasons = []
    if not record.name:
        reasons.append("नाम गायब")
    if not record.epic_id:
        reasons.append("EPIC गायब")
    if not record.age:
        reasons.append("आयु गायब/अमान्य")
    if not record.gender:
        reasons.append("लिंग गायब")
    if not record.serial_number:
        reasons.append("क्रम संख्या OCR से नहीं मिली")
    if record.confidence < min_confidence:
        reasons.append("कम OCR विश्वास")
    record.review_required = bool(reasons)
    record.review_reason = "; ".join(reasons)
    return record


def record_presence_score(record: VoterRecord) -> int:
    return sum(
        1
        for value in (
            record.serial_number,
            record.epic_id,
            record.name,
            record.related_person_name,
            record.house_number,
            record.age,
            record.gender,
        )
        if value
    )


def choose_better_record(first: VoterRecord, second: VoterRecord) -> VoterRecord:
    first_score = record_presence_score(first) * 10 + first.confidence
    second_score = record_presence_score(second) * 10 + second.confidence
    return second if second_score > first_score else first


def reconcile_serials(records: List[VoterRecord]) -> None:
    if not records:
        return
    offsets = []
    for index, record in enumerate(records):
        if record.serial_number.isdigit():
            value = int(record.serial_number)
            if 1 <= value <= 999999:
                offsets.append(value - index)
    if len(offsets) < 5:
        return
    offset, count = Counter(offsets).most_common(1)[0]
    if count < max(5, round(len(records) * 0.25)):
        return
    for index, record in enumerate(records):
        expected = index + offset
        if expected <= 0:
            continue
        current = int(record.serial_number) if record.serial_number.isdigit() else None
        if current is None or abs(current - expected) > 2:
            old = record.serial_number
            record.serial_number = str(expected)
            record.serial_inferred = True
            record.review_required = True
            note = "क्रम संख्या क्रम के आधार पर भरी गई"
            if old:
                note += " (OCR: {})".format(old)
            record.review_reason = (record.review_reason + "; " if record.review_reason else "") + note
