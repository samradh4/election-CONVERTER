from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import List, Sequence, Tuple

import cv2
import numpy as np
import pytesseract
from pytesseract import Output

from .config import RESOURCE_ROOT, TESSDATA_DIR
from .models import OCRWord
from .utils import normalize_text


def _configure_tesseract() -> None:
    candidates = [
        RESOURCE_ROOT / "tesseract" / "tesseract.exe",
        Path(sys.executable).resolve().parent / "tesseract" / "tesseract.exe",
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
        Path("/opt/homebrew/bin/tesseract"),
        Path("/usr/local/bin/tesseract"),
    ]

    bundled = RESOURCE_ROOT / "tesseract" / "tesseract.exe"
    if bundled.is_file():
        pytesseract.pytesseract.tesseract_cmd = str(bundled)
    elif not shutil.which("tesseract"):
        for candidate in candidates[1:]:
            if candidate.is_file():
                pytesseract.pytesseract.tesseract_cmd = str(candidate)
                break

    if TESSDATA_DIR.is_dir():
        os.environ["TESSDATA_PREFIX"] = str(TESSDATA_DIR)


def _tessdata_config() -> str:
    return ""


def validate_ocr_languages(languages: str) -> None:
    _configure_tesseract()
    try:
        available = set(pytesseract.get_languages(config=_tessdata_config()))
    except Exception as exc:
        raise RuntimeError(
            "Bundled Tesseract OCR could not start. Re-download the latest Voter List Converter."
        ) from exc
    missing = [lang for lang in languages.split("+") if lang and lang not in available]
    if missing:
        raise RuntimeError(
            "Bundled OCR language data is missing: {}. Re-download the latest Voter List Converter.".format(
                ", ".join(missing)
            )
        )


def _prepare_image(image: np.ndarray, strong: bool = False) -> np.ndarray:
    if image.ndim == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    else:
        gray = image.copy()
    if gray.size == 0:
        return gray
    scale = 1.45 if strong else 1.15
    gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    gray = cv2.fastNlMeansDenoising(gray, None, 8 if strong else 5, 7, 21)
    clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8))
    gray = clahe.apply(gray)
    return gray


def image_to_words(
    image: np.ndarray,
    languages: str,
    psm: int = 11,
    strong: bool = False,
) -> List[OCRWord]:
    _configure_tesseract()
    prepared = _prepare_image(image, strong=strong)
    config = "--oem 1 --psm {}".format(int(psm))
    data = pytesseract.image_to_data(
        prepared, lang=languages, config=config, output_type=Output.DICT
    )
    scale_x = image.shape[1] / prepared.shape[1]
    scale_y = image.shape[0] / prepared.shape[0]
    words: List[OCRWord] = []
    total = len(data.get("text", []))
    for index in range(total):
        text = normalize_text(data["text"][index])
        if not text:
            continue
        try:
            confidence = float(data["conf"][index])
        except (TypeError, ValueError):
            confidence = -1.0
        if confidence < 0:
            continue
        left = round(int(data["left"][index]) * scale_x)
        top = round(int(data["top"][index]) * scale_y)
        width = round(int(data["width"][index]) * scale_x)
        height = round(int(data["height"][index]) * scale_y)
        words.append(
            OCRWord(
                text=text,
                left=left,
                top=top,
                right=left + width,
                bottom=top + height,
                confidence=confidence,
                line_key=(
                    int(data["block_num"][index]),
                    int(data["par_num"][index]),
                    int(data["line_num"][index]),
                ),
            )
        )
    return words


def crop_to_text(
    image: np.ndarray, languages: str, psm: int = 6, strong: bool = True
) -> Tuple[str, float]:
    _configure_tesseract()
    prepared = _prepare_image(image, strong=strong)
    config = "--oem 1 --psm {} -c preserve_interword_spaces=1".format(int(psm))
    data = pytesseract.image_to_data(
        prepared, lang=languages, config=config, output_type=Output.DICT
    )
    lines = {}
    confidences: List[float] = []
    total = len(data.get("text", []))
    for index in range(total):
        text = normalize_text(data["text"][index])
        if not text:
            continue
        try:
            conf = float(data["conf"][index])
        except (TypeError, ValueError):
            conf = -1.0
        if conf >= 0:
            confidences.append(conf)
        key = (
            int(data["block_num"][index]),
            int(data["par_num"][index]),
            int(data["line_num"][index]),
        )
        lines.setdefault(key, []).append(text)
    ordered = [" ".join(lines[key]) for key in sorted(lines)]
    text = normalize_text("\n".join(ordered))
    confidence = (sum(confidences) / len(confidences) / 100.0) if confidences else 0.0
    return text, max(0.0, min(1.0, confidence))


def words_in_box(words: Sequence[OCRWord], box: Tuple[int, int, int, int]) -> List[OCRWord]:
    x, y, width, height = box
    x2, y2 = x + width, y + height
    selected = []
    for word in words:
        center_x, center_y = word.center
        if x <= center_x <= x2 and y <= center_y <= y2:
            selected.append(word)
    return selected


def words_to_text(words: Sequence[OCRWord]) -> Tuple[str, float]:
    if not words:
        return "", 0.0
    grouped = {}
    for word in words:
        key = word.line_key or (round(word.top / 12),)
        grouped.setdefault(key, []).append(word)
    lines = []
    for key, line_words in sorted(
        grouped.items(), key=lambda item: min(word.top for word in item[1])
    ):
        line_words = sorted(line_words, key=lambda word: word.left)
        lines.append(" ".join(word.text for word in line_words))
    positive = [word.confidence for word in words if word.confidence >= 0]
    confidence = (sum(positive) / len(positive) / 100.0) if positive else 0.0
    return normalize_text("\n".join(lines)), max(0.0, min(1.0, confidence))
