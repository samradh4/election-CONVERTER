from __future__ import annotations

import re

from .utils import normalize_text

_APPLIED = False
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_DIGIT_FIX = str.maketrans(
    {
        "O": "0",
        "Q": "0",
        "D": "0",
        "I": "1",
        "L": "1",
        "Z": "2",
        "S": "5",
        "B": "8",
        "G": "6",
    }
)
_NEW_EPIC = re.compile(r"^[A-Z]{3}[0-9]{7}$")
_LEGACY_EPIC = re.compile(r"^[A-Z]{2}/[0-9]{2}/[0-9]{3}/[0-9]{7}$")


def valid_epic(value: str) -> bool:
    text = (value or "").strip().upper()
    return bool(_NEW_EPIC.fullmatch(text) or _LEGACY_EPIC.fullmatch(text))


def _digits(value: str, expected: int) -> str:
    fixed = value.upper().translate(_DIGIT_FIX)
    digits = re.sub(r"[^0-9]", "", fixed)
    return digits if len(digits) == expected else ""


def extract_epic(value: str) -> str:
    """Extract both modern and legacy Indian EPIC formats.

    Modern IDs use three letters plus seven digits (UCC0700005). Older rolls
    also contain IDs such as UP/57/277/0036003. Whitespace and slash loss are
    tolerated, while an extra adjacent digit is rejected instead of silently
    truncating an ambiguous identifier.
    """
    text = normalize_text(value or "").translate(_DEVANAGARI_DIGITS).upper()

    # Modern format with a real boundary after seven digits. This correctly
    # handles OCR such as "UCC3594744 7" without treating the trailing cell
    # artefact as part of the EPIC number.
    modern = re.search(
        r"(?<![A-Z0-9])([A-Z]{3})\s*[-_/.:]?\s*([A-Z0-9]{7})(?![0-9])",
        text,
    )
    if modern:
        suffix = _digits(modern.group(2), 7)
        if suffix:
            candidate = modern.group(1) + suffix
            if valid_epic(candidate):
                return candidate

    # Packed OCR sometimes appends one or two letters from the photo box after
    # an otherwise valid modern EPIC. Accept letters only; never truncate an
    # additional digit.
    for token in re.findall(r"[A-Z0-9]{10,13}", re.sub(r"[\s\-_/.:]", "", text)):
        if not token[:3].isalpha():
            continue
        tail = token[10:]
        if tail and not tail.isalpha():
            continue
        suffix = _digits(token[3:10], 7)
        if suffix:
            candidate = token[:3] + suffix
            if valid_epic(candidate):
                return candidate

    # Legacy format: two letters / two digits / three digits / seven digits.
    # Separators may be printed, omitted, or misread as spaces.
    legacy = re.search(
        r"(?<![A-Z0-9])([A-Z]{2})\s*[/\-_.:]?\s*([A-Z0-9]{2})\s*[/\-_.:]?\s*([A-Z0-9]{3})\s*[/\-_.:]?\s*([A-Z0-9]{7})(?![0-9])",
        text,
    )
    if legacy:
        first = _digits(legacy.group(2), 2)
        second = _digits(legacy.group(3), 3)
        third = _digits(legacy.group(4), 7)
        if first and second and third:
            candidate = "{}/{}/{}/{}".format(
                legacy.group(1), first, second, third
            )
            if valid_epic(candidate):
                return candidate

    # Compact legacy OCR can have trailing letters from a neighbouring border.
    compact = re.sub(r"[\s\-_/.:]", "", text)
    for token in re.findall(r"[A-Z0-9]{14,17}", compact):
        if not token[:2].isalpha():
            continue
        tail = token[14:]
        if tail and not tail.isalpha():
            continue
        first = _digits(token[2:4], 2)
        second = _digits(token[4:7], 3)
        third = _digits(token[7:14], 7)
        if first and second and third:
            candidate = "{}/{}/{}/{}".format(token[:2], first, second, third)
            if valid_epic(candidate):
                return candidate

    return ""


def apply() -> None:
    global _APPLIED
    if _APPLIED:
        return
    _APPLIED = True

    from . import accuracy_patch, parser, strict_reader

    accuracy_patch.valid_epic = valid_epic
    accuracy_patch.extract_epic = extract_epic
    parser._extract_epic = extract_epic
    strict_reader._valid_epic = valid_epic
    strict_reader._extract_epic = extract_epic
