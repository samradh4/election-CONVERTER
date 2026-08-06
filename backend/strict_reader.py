from __future__ import annotations

import math
import re
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple, Union

import cv2
import numpy as np
import pytesseract
from pytesseract import Output

from .config import ConversionConfig
from .layout import crop_box, detect_voter_boxes, estimate_photo_available
from .models import ConversionResult, PageResult, VoterRecord
from .ocr import image_to_words, validate_ocr_languages, words_in_box, words_to_text
from .parser import choose_better_record, merge_metadata, parse_metadata, parse_record
from .pdf_reader import get_page_count, render_page, text_layer_is_usable
from .utils import normalize_text

ProgressCallback = Callable[[int, int, str], None]
Box = Tuple[int, int, int, int]

_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_EPIC_DIGIT_FIX = str.maketrans({"O": "0", "Q": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "B": "8", "G": "6"})
_VALID_EPIC = re.compile(r"^[A-Z]{3}[0-9]{7}$")


def _valid_epic(value: str) -> bool:
    return bool(_VALID_EPIC.fullmatch((value or "").strip().upper()))


def _extract_epic(value: str) -> str:
    text = normalize_text(value or "").translate(_DEVANAGARI_DIGITS).upper()
    compact_tokens = re.findall(r"[A-Z0-9]{9,12}", re.sub(r"[\s\-_/.:]", "", text))
    for token in compact_tokens:
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


def _extract_age_digits(value: str) -> str:
    text = (value or "").translate(_DEVANAGARI_DIGITS)
    for token in re.findall(r"(?<!\d)(\d{1,3})(?!\d)", text):
        number = int(token)
        if 18 <= number <= 120:
            return str(number)
    return ""


def _batch_region_texts(
    image: np.ndarray,
    boxes: Sequence[Box],
    region: Tuple[float, float, float, float],
    languages: str,
    config: str,
    scale: float = 2.0,
    columns: int = 3,
    padding: int = 20,
) -> List[str]:
    if not boxes:
        return []
    x1r, x2r, y1r, y2r = region
    crops: List[np.ndarray] = []
    max_width = 1
    max_height = 1
    for x, y, width, height in boxes:
        x1 = max(0, x + round(width * x1r))
        x2 = min(image.shape[1], x + round(width * x2r))
        y1 = max(0, y + round(height * y1r))
        y2 = min(image.shape[0], y + round(height * y2r))
        crop = image[y1:y2, x1:x2]
        if crop.size == 0:
            crop = np.full((8, 8, 3), 255, dtype=np.uint8)
        crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        crops.append(crop)
        max_height = max(max_height, crop.shape[0])
        max_width = max(max_width, crop.shape[1])

    columns = max(1, min(columns, len(crops)))
    rows = math.ceil(len(crops) / columns)
    cell_width = max_width + padding * 2
    cell_height = max_height + padding * 2
    canvas = np.full((rows * cell_height, columns * cell_width, 3), 255, dtype=np.uint8)
    placements: List[Tuple[int, int, int, int]] = []
    for index, crop in enumerate(crops):
        row = index // columns
        column = index % columns
        left = column * cell_width + padding
        top = row * cell_height + padding
        canvas[top : top + crop.shape[0], left : left + crop.shape[1]] = crop
        placements.append((left, top, crop.shape[1], crop.shape[0]))

    gray = cv2.cvtColor(canvas, cv2.COLOR_RGB2GRAY)
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    data = pytesseract.image_to_data(
        gray,
        lang=languages,
        config=config,
        output_type=Output.DICT,
    )
    words: List[Tuple[str, int, int, int, int]] = []
    for index, raw in enumerate(data.get("text", [])):
        text = normalize_text(raw)
        if not text:
            continue
        try:
            confidence = float(data["conf"][index])
        except (TypeError, ValueError):
            confidence = -1.0
        if confidence < 0:
            continue
        left = int(data["left"][index])
        top = int(data["top"][index])
        width = int(data["width"][index])
        height = int(data["height"][index])
        words.append((text, left, top, left + width, top + height))

    result: List[str] = []
    for left, top, width, height in placements:
        right = left + width
        bottom = top + height
        selected = [
            word
            for word in words
            if left <= (word[1] + word[3]) / 2.0 <= right
            and top <= (word[2] + word[4]) / 2.0 <= bottom
        ]
        selected.sort(key=lambda item: (item[2], item[1]))
        result.append(normalize_text(" ".join(item[0] for item in selected)))
    return result


def _read_age_region(card_image: np.ndarray) -> str:
    if card_image.size == 0:
        return ""
    height, width = card_image.shape[:2]
    crop = card_image[
        round(height * 0.56) : max(round(height * 0.90), round(height * 0.56) + 1),
        : max(1, round(width * 0.72)),
    ]
    if crop.size == 0:
        return ""
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    gray = cv2.resize(gray, None, fx=2.8, fy=2.8, interpolation=cv2.INTER_CUBIC)
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    digit_text = pytesseract.image_to_string(
        gray,
        lang="eng",
        config="--oem 1 --psm 7 -c tessedit_char_whitelist=0123456789",
    )
    rescued = _extract_age_digits(digit_text)
    if rescued:
        return rescued
    normal_text = normalize_text(
        pytesseract.image_to_string(gray, lang="hin+eng", config="--oem 1 --psm 7")
    )
    match = re.search(
        r"(?:आयु|उम्र|AGE)\s*[:\-]?\s*([0-9०-९]{1,3})",
        normal_text,
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    value = match.group(1).translate(_DEVANAGARI_DIGITS)
    return value if value.isdigit() and 18 <= int(value) <= 120 else ""


def _header_metadata(image: np.ndarray, fallback_text: str) -> Dict[str, str]:
    metadata = parse_metadata(fallback_text)
    height, width = image.shape[:2]
    part_crop = image[: max(1, round(height * 0.06)), round(width * 0.65) :]
    part_gray = cv2.cvtColor(part_crop, cv2.COLOR_RGB2GRAY)
    part_gray = cv2.resize(part_gray, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_CUBIC)
    part_text = pytesseract.image_to_string(
        part_gray,
        lang="eng",
        config="--oem 1 --psm 6 -c tessedit_char_whitelist=0123456789",
    )
    part_tokens = [token for token in re.findall(r"\d{1,4}", part_text) if 0 < int(token) < 10000]
    if part_tokens:
        metadata["part_number"] = part_tokens[0]

    header_crop = image[: max(1, round(height * 0.058)), :]
    header_gray = cv2.cvtColor(header_crop, cv2.COLOR_RGB2GRAY)
    header_gray = cv2.resize(header_gray, None, fx=2.2, fy=2.2, interpolation=cv2.INTER_CUBIC)
    header_text = normalize_text(
        pytesseract.image_to_string(header_gray, lang="hin+eng", config="--oem 1 --psm 6")
    )
    repaired = parse_metadata(header_text)
    for key in ("constituency", "section"):
        if repaired.get(key):
            metadata[key] = repaired[key]
    return metadata


def _record_from_text(
    text: str,
    page_number: int,
    card_number: int,
    metadata: Dict[str, str],
    photo: bool,
    confidence: float = 0.82,
) -> VoterRecord:
    return parse_record(
        text,
        page_number=page_number,
        card_number=card_number,
        metadata=metadata,
        ocr_confidence=confidence,
        photo_available=photo,
        min_confidence=0.68,
    )


def _finalize_record(record: VoterRecord) -> None:
    if record.epic_id and not _valid_epic(record.epic_id):
        record.epic_id = ""
    reasons: List[str] = []
    checks = (
        (record.serial_number, "क्रम संख्या गायब"),
        (record.epic_id, "EPIC गायब/अमान्य"),
        (record.name, "नाम गायब"),
        (record.relation, "संबंध गायब"),
        (record.related_person_name, "संबंधित व्यक्ति का नाम गायब"),
        (record.house_number, "मकान संख्या गायब"),
        (record.age, "आयु गायब/अमान्य"),
        (record.gender, "लिंग गायब"),
    )
    for value, reason in checks:
        if not value:
            reasons.append(reason)
    record.review_required = bool(reasons)
    record.review_reason = "; ".join(reasons)


def _process_page(pdf_path: str, page_index: int, config: ConversionConfig) -> PageResult:
    settings = config.settings
    page_number = page_index + 1
    image, digital_words, digital_text = render_page(pdf_path, page_index, settings.dpi)
    use_text = text_layer_is_usable(digital_words, digital_text)
    if use_text:
        page_words = digital_words
        page_text = digital_text
    else:
        page_words = image_to_words(image, config.languages, psm=11, strong=False)
        page_text, _ = words_to_text(page_words)

    boxes, method, voter_page = detect_voter_boxes(image, page_words, page_text)
    metadata = _header_metadata(image, "\n".join(part for part in (digital_text, page_text) if part))
    if not voter_page:
        return PageResult(
            page_number=page_number,
            metadata=metadata,
            used_text_layer=use_text,
            detected_boxes=0,
            layout_method=method,
            voter_page=False,
        )

    records: List[VoterRecord] = []
    for card_number, box in enumerate(boxes, start=1):
        card_words = words_in_box(page_words, box)
        text, confidence = words_to_text(card_words)
        photo = estimate_photo_available(image, box)
        records.append(
            _record_from_text(
                text,
                page_number,
                card_number,
                metadata,
                photo,
                confidence=confidence,
            )
        )

    epic_texts = _batch_region_texts(
        image,
        boxes,
        region=(0.40, 1.0, 0.0, 0.30),
        languages="eng",
        config="--oem 1 --psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        scale=2.2,
    )
    for record, epic_text in zip(records, epic_texts):
        dedicated_epic = _extract_epic(epic_text)
        quick_epic = record.epic_id if _valid_epic(record.epic_id) else ""
        record.epic_id = dedicated_epic or quick_epic
        if epic_text:
            record.raw_text = normalize_text(
                "\n".join(part for part in (record.raw_text, "EPIC OCR: " + epic_text) if part)
            )

    body_indexes = [
        index
        for index, record in enumerate(records)
        if not all(
            (
                record.name,
                record.relation,
                record.related_person_name,
                record.house_number,
                record.age,
                record.gender,
            )
        )
    ]
    if body_indexes:
        body_boxes = [boxes[index] for index in body_indexes]
        body_texts = _batch_region_texts(
            image,
            body_boxes,
            region=(0.0, 0.76, 0.08, 0.92),
            languages=config.languages,
            config="--oem 1 --psm 6 -c preserve_interword_spaces=1",
            scale=1.8,
        )
        for record_index, body_text in zip(body_indexes, body_texts):
            record = records[record_index]
            retry = _record_from_text(
                body_text,
                page_number,
                record.source_card,
                metadata,
                record.photo_available == "हाँ",
                confidence=0.86,
            )
            merged = choose_better_record(record, retry)
            merged.epic_id = record.epic_id or merged.epic_id
            records[record_index] = merged

    for record_index, record in enumerate(records):
        if not record.age:
            rescued = _read_age_region(crop_box(image, boxes[record_index], padding=3))
            if rescued:
                record.age = rescued
        _finalize_record(record)

    warnings: List[str] = []
    if len(records) != len(boxes):
        warnings.append(
            "पृष्ठ {}: {} कार्ड मिले, लेकिन {} रिकॉर्ड बने।".format(
                page_number, len(boxes), len(records)
            )
        )
    return PageResult(
        page_number=page_number,
        records=records,
        metadata=metadata,
        warnings=warnings,
        used_text_layer=use_text,
        detected_boxes=len(boxes),
        layout_method=method,
        voter_page=True,
    )


def _serial_number(value: str) -> Optional[int]:
    text = (value or "").translate(_DEVANAGARI_DIGITS)
    return int(text) if text.isdigit() and 0 < int(text) < 1000000 else None


def _reconcile_serials(records: List[VoterRecord]) -> int:
    if not records:
        return 0
    corrected = 0
    offsets: Counter[int] = Counter()
    anchors = 0
    for index, record in enumerate(records):
        number = _serial_number(record.serial_number)
        if number is None:
            continue
        anchors += 1
        offsets[number - index] += 1

    if offsets:
        global_offset, support = offsets.most_common(1)[0]
        strong_global = support >= max(12, round(len(records) * 0.05)) and support >= max(4, round(anchors * 0.18))
        if strong_global:
            for index, record in enumerate(records):
                expected = index + global_offset
                if expected > 0 and _serial_number(record.serial_number) != expected:
                    corrected += 1
                    record.serial_number = str(expected)
                    record.serial_inferred = True
            return corrected

    by_page: Dict[int, List[VoterRecord]] = defaultdict(list)
    for record in records:
        by_page[record.source_page].append(record)
    for page_records in by_page.values():
        page_records.sort(key=lambda item: item.source_card)
        page_offsets: Counter[int] = Counter()
        for record in page_records:
            number = _serial_number(record.serial_number)
            if number is not None:
                page_offsets[number - record.source_card] += 1
        if not page_offsets:
            continue
        offset, support = page_offsets.most_common(1)[0]
        if support < max(3, round(len(page_records) * 0.20)):
            continue
        for record in page_records:
            expected = record.source_card + offset
            if expected > 0 and _serial_number(record.serial_number) != expected:
                corrected += 1
                record.serial_number = str(expected)
                record.serial_inferred = True
    return corrected


def convert_pdf_strict(
    pdf_path: Union[str, Path],
    config: Optional[ConversionConfig] = None,
    progress_callback: Optional[ProgressCallback] = None,
) -> ConversionResult:
    config = config or ConversionConfig(mode="turbo")
    path = str(Path(pdf_path).resolve())
    total_pages = get_page_count(path)
    if total_pages <= 0:
        raise ValueError("PDF has no pages.")
    validate_ocr_languages(config.languages)

    if progress_callback:
        progress_callback(0, total_pages, "Strict quality scan शुरू हो रहा है")
    started = time.perf_counter()
    results: List[PageResult] = []
    completed = 0
    lock = threading.Lock()
    workers = max(1, min(4, config.settings.workers))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_process_page, path, page_index, config): page_index
            for page_index in range(total_pages)
        }
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            with lock:
                completed += 1
                if progress_callback:
                    progress_callback(
                        completed,
                        total_pages,
                        "पृष्ठ {}/{} पूरा".format(completed, total_pages),
                    )

    results.sort(key=lambda item: item.page_number)
    records = [record for result in results for record in result.records]
    if not records:
        raise ValueError("No voter cards were detected in the PDF.")

    overrides: Dict[str, str] = {}
    if config.use_manual_metadata:
        overrides = {
            "constituency": config.constituency_override,
            "section": config.section_override,
            "part_number": config.part_override,
        }
    metadata = merge_metadata((result.metadata for result in results), overrides)
    for record in records:
        if config.use_manual_metadata:
            record.constituency = overrides.get("constituency") or record.constituency
            record.section = overrides.get("section") or record.section
            record.part_number = overrides.get("part_number") or record.part_number
        else:
            record.constituency = record.constituency or metadata.get("constituency", "")
            record.section = record.section or metadata.get("section", "")
            record.part_number = record.part_number or metadata.get("part_number", "")

    serial_corrections = _reconcile_serials(records)

    epic_counts = Counter(record.epic_id for record in records if _valid_epic(record.epic_id))
    for record in records:
        duplicate = bool(record.epic_id and epic_counts[record.epic_id] > 1)
        record.duplicate_epic = duplicate
        _finalize_record(record)
        if duplicate:
            record.review_required = True
            record.review_reason = (record.review_reason + "; " if record.review_reason else "") + "डुप्लिकेट EPIC"

    voter_pages = [result for result in results if result.voter_page]
    detected_cards = sum(result.detected_boxes for result in voter_pages)
    warnings = [warning for result in results for warning in result.warnings]
    warnings.insert(0, "Strict Reader: {} voter pages और {} कार्ड मिले।".format(len(voter_pages), detected_cards))
    warnings.insert(1, "{} रिकॉर्ड export हुए; कोई detected card चुपचाप हटाया नहीं गया।".format(len(records)))
    warnings.insert(2, "{} क्रम संख्याएँ मजबूत sequence evidence से सुधारी गईं।".format(serial_corrections))

    if detected_cards != len(records):
        raise RuntimeError(
            "Quality gate failed: {} detected cards but {} records were produced. No incomplete Excel was returned.".format(
                detected_cards, len(records)
            )
        )

    missing_epic = sum(1 for record in records if not _valid_epic(record.epic_id))
    missing_age = sum(1 for record in records if not record.age)
    missing_gender = sum(1 for record in records if not record.gender)
    warnings.append(
        "Quality check: EPIC missing {}, age missing {}, gender missing {}.".format(
            missing_epic, missing_age, missing_gender
        )
    )

    elapsed = time.perf_counter() - started
    review_count = sum(1 for record in records if record.review_required)
    if progress_callback:
        progress_callback(total_pages, total_pages, "Excel बनाई जा रही है")
    return ConversionResult(
        records=records,
        metadata=metadata,
        page_count=total_pages,
        warnings=warnings,
        elapsed_seconds=elapsed,
        review_count=review_count,
        detected_card_count=detected_cards,
    )
