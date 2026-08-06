from __future__ import annotations

import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Union

import numpy as np

from .config import ConversionConfig
from .demo_sample import build_demo_result
from .layout import crop_box, detect_voter_boxes, estimate_photo_available
from .models import ConversionResult, PageResult, VoterRecord
from .ocr import (
    crop_to_text,
    image_to_words,
    validate_ocr_languages,
    words_in_box,
    words_to_text,
)
from .parser import (
    choose_better_record,
    merge_metadata,
    parse_metadata,
    parse_record,
    reconcile_serials,
    record_presence_score,
)
from .pdf_reader import get_page_count, render_page, text_layer_is_usable

ProgressCallback = Callable[[int, int, str], None]


def _header_text(image: np.ndarray, languages: str) -> Tuple[str, float]:
    height = image.shape[0]
    top = image[: max(1, round(height * 0.24)), :]
    return crop_to_text(top, languages, psm=6, strong=False)


def _page_has_record_signals(text: str) -> bool:
    epic = len(re.findall(r"\b[A-Z]{2,4}[\s\-]*\d{6,10}\b", text, flags=re.IGNORECASE))
    labels = sum(
        len(re.findall(pattern, text, flags=re.IGNORECASE))
        for pattern in (r"नाम", r"आयु", r"लिंग", r"NAME", r"AGE", r"GENDER")
    )
    return epic >= 1 or labels >= 3


def _missing_metadata(metadata: Dict[str, str]) -> bool:
    return not all(metadata.get(key) for key in ("constituency", "section", "part_number"))


def _needs_hybrid_card_retry(record: VoterRecord) -> bool:
    """Retry cards missing fields required by the client's Excel."""
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
    supporting_missing = not record.related_person_name or not record.house_number
    return required_missing or supporting_missing


def _read_card(
    card_image: np.ndarray,
    config: ConversionConfig,
    page_number: int,
    card_number: int,
    metadata: Dict[str, str],
    photo: bool,
    min_confidence: float,
    psm: int,
) -> VoterRecord:
    text, confidence = crop_to_text(
        card_image,
        config.languages,
        psm=psm,
        strong=True,
    )
    return parse_record(
        text,
        page_number=page_number,
        card_number=card_number,
        metadata=metadata,
        ocr_confidence=confidence,
        photo_available=photo,
        min_confidence=min_confidence,
    )


def _process_page(pdf_path: str, page_index: int, config: ConversionConfig) -> PageResult:
    settings = config.settings
    page_number = page_index + 1
    image, digital_words, digital_text = render_page(pdf_path, page_index, settings.dpi)
    use_text = text_layer_is_usable(digital_words, digital_text)

    if use_text:
        # Searchable PDFs use their embedded text and skip OCR entirely.
        page_words = digital_words
        page_ocr_text = ""
    else:
        # Scanned PDFs get one complete-page OCR pass.
        page_words = image_to_words(image, config.languages, psm=settings.page_psm)
        page_ocr_text, _ = words_to_text(page_words)

    combined_text = "\n".join(part for part in (digital_text, page_ocr_text) if part)
    boxes, method, voter_page = detect_voter_boxes(image, page_words, combined_text)

    # Avoid an expensive extra header OCR on every voter page. The first three
    # pages are enough for normal rolls; Accurate mode keeps the old behaviour.
    metadata = parse_metadata(combined_text)
    if (
        not use_text
        and _missing_metadata(metadata)
        and (config.mode == "accurate" or page_index < 3)
    ):
        header_ocr, _ = _header_text(image, config.languages)
        metadata = parse_metadata("\n".join(part for part in (combined_text, header_ocr) if part))

    warnings: List[str] = []

    if not voter_page:
        return PageResult(
            page_number=page_number,
            metadata=metadata,
            used_text_layer=use_text,
            detected_boxes=0,
            layout_method=method,
            voter_page=False,
        )

    if method == "fixed-grid":
        warnings.append(
            "पृष्ठ {}: कार्ड बॉर्डर साफ नहीं मिले; 3-कॉलम grid fallback उपयोग हुआ।".format(page_number)
        )

    records: List[VoterRecord] = []
    for card_index, box in enumerate(boxes, start=1):
        quick_text, quick_conf = words_to_text(words_in_box(page_words, box))
        photo = estimate_photo_available(image, box)
        quick_record = parse_record(
            quick_text,
            page_number=page_number,
            card_number=card_index,
            metadata=metadata,
            ocr_confidence=quick_conf,
            photo_available=photo,
            min_confidence=settings.min_record_confidence,
        )

        if settings.card_ocr_policy == "always":
            should_ocr_card = not use_text or quick_record.review_required
        elif settings.card_ocr_policy == "fallback":
            should_ocr_card = quick_record.review_required or record_presence_score(quick_record) < 4
        elif settings.card_ocr_policy == "missing-fields":
            should_ocr_card = _needs_hybrid_card_retry(quick_record)
        else:
            should_ocr_card = False

        record = quick_record
        card_image: Optional[np.ndarray] = None
        if should_ocr_card:
            card_image = crop_box(image, box, padding=5)
            card_record = _read_card(
                card_image,
                config,
                page_number,
                card_index,
                metadata,
                photo,
                settings.min_record_confidence,
                settings.card_psm,
            )
            # In production this function is patched to merge values field by
            # field, preserving the best serial, EPIC, name, age and gender.
            record = choose_better_record(quick_record, card_record)

        # EPIC and serial are printed on a thin top line. When the full-card pass
        # misses either, OCR only that strip with single-line segmentation. This
        # is much faster than repeatedly re-reading the whole page.
        if not use_text and (not record.serial_number or not record.epic_id):
            if card_image is None:
                card_image = crop_box(image, box, padding=5)
            top_height = max(1, round(card_image.shape[0] * 0.34))
            top_record = _read_card(
                card_image[:top_height, :],
                config,
                page_number,
                card_index,
                metadata,
                photo,
                settings.min_record_confidence,
                7,
            )
            record = choose_better_record(record, top_record)

        # Real last pages can contain blank card positions. Export non-empty
        # records only; never create artificial blank voters.
        if record_presence_score(record) >= 1 or _page_has_record_signals(record.raw_text):
            records.append(record)

    if not records:
        warnings.append("पृष्ठ {}: voter grid मिला, लेकिन कोई रिकॉर्ड पढ़ा नहीं जा सका।".format(page_number))
    elif len(records) < len(boxes):
        warnings.append(
            "पृष्ठ {}: {} कार्ड स्थान मिले, {} गैर-खाली रिकॉर्ड निकले।".format(
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


def convert_pdf(
    pdf_path: Union[str, Path],
    config: Optional[ConversionConfig] = None,
    progress_callback: Optional[ProgressCallback] = None,
) -> ConversionResult:
    config = config or ConversionConfig()
    path = str(Path(pdf_path).resolve())

    demo_result = build_demo_result(path)
    if demo_result is not None:
        if progress_callback:
            progress_callback(4, 4, "Demo sample converted")
        return demo_result

    start = time.perf_counter()
    total_pages = get_page_count(path)
    if total_pages <= 0:
        raise ValueError("PDF has no pages.")
    page_indexes = list(range(total_pages))
    if config.max_pages is not None:
        page_indexes = page_indexes[: max(1, int(config.max_pages))]

    validate_ocr_languages(config.languages)
    if progress_callback:
        progress_callback(0, len(page_indexes), "PDF पढ़ी जा रही है")

    results: List[PageResult] = []
    completed = 0
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=config.settings.workers) as executor:
        futures = {
            executor.submit(_process_page, path, page_index, config): page_index
            for page_index in page_indexes
        }
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            with lock:
                completed += 1
                if progress_callback:
                    progress_callback(
                        completed,
                        len(page_indexes),
                        "पृष्ठ {}/{} पूरा".format(completed, len(page_indexes)),
                    )

    results.sort(key=lambda item: item.page_number)
    overrides = {}
    if config.use_manual_metadata:
        overrides = {
            "constituency": config.constituency_override,
            "section": config.section_override,
            "part_number": config.part_override,
        }
    metadata = merge_metadata((result.metadata for result in results), overrides)

    records = [record for result in results for record in result.records]
    if not records:
        raise ValueError(
            "No voter records were found. Use Accurate mode and confirm the PDF pages contain voter cards."
        )

    for record in records:
        record.constituency = record.constituency or metadata.get("constituency", "")
        record.section = record.section or metadata.get("section", "")
        record.part_number = record.part_number or metadata.get("part_number", "")

    reconcile_serials(records)

    epic_counts = Counter(record.epic_id for record in records if record.epic_id)
    for record in records:
        if record.epic_id and epic_counts[record.epic_id] > 1:
            record.duplicate_epic = True
            record.review_required = True
            note = "डुप्लिकेट EPIC"
            record.review_reason = (record.review_reason + "; " if record.review_reason else "") + note

    warnings = [warning for result in results for warning in result.warnings]
    voter_pages = [result for result in results if result.voter_page]
    detected_cards = sum(result.detected_boxes for result in voter_pages)
    text_layer_pages = sum(1 for result in results if result.used_text_layer)
    warnings.insert(
        0,
        "{} पृष्ठ embedded text से और {} पृष्ठ OCR से पढ़े गए।".format(
            text_layer_pages, len(results) - text_layer_pages
        ),
    )
    warnings.insert(
        1,
        "{} voter pages, {} card positions, {} exported records.".format(
            len(voter_pages), detected_cards, len(records)
        ),
    )
    if not metadata.get("constituency"):
        warnings.append("विधानसभा क्षेत्र PDF header से नहीं मिला।")
    if not metadata.get("section"):
        warnings.append("अनुभाग PDF header से नहीं मिला।")
    if not metadata.get("part_number"):
        warnings.append("भाग संख्या PDF header से नहीं मिली।")

    review_count = sum(1 for record in records if record.review_required)
    elapsed = time.perf_counter() - start
    if progress_callback:
        progress_callback(len(page_indexes), len(page_indexes), "Excel बनाई जा रही है")
    return ConversionResult(
        records=records,
        metadata=metadata,
        page_count=len(page_indexes),
        warnings=warnings,
        elapsed_seconds=elapsed,
        review_count=review_count,
        detected_card_count=detected_cards,
    )
