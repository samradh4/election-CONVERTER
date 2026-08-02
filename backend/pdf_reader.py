from __future__ import annotations

import re
from pathlib import Path
from typing import List, Tuple, Union

import fitz
import numpy as np

from .models import OCRWord
from .utils import normalize_text


def get_page_count(pdf_path: Union[str, Path]) -> int:
    with fitz.open(str(pdf_path)) as document:
        return document.page_count


def render_page(
    pdf_path: Union[str, Path], page_index: int, dpi: int
) -> Tuple[np.ndarray, List[OCRWord], str]:
    with fitz.open(str(pdf_path)) as document:
        page = document.load_page(page_index)
        scale = dpi / 72.0
        pixmap = page.get_pixmap(
            matrix=fitz.Matrix(scale, scale), alpha=False, colorspace=fitz.csRGB
        )
        image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
            pixmap.height, pixmap.width, 3
        ).copy()
        raw_words = page.get_text("words", sort=True)
        words: List[OCRWord] = []
        for item in raw_words:
            x0, y0, x1, y1, text, block_no, line_no, word_no = item[:8]
            text = normalize_text(str(text))
            if not text:
                continue
            words.append(
                OCRWord(
                    text=text,
                    left=round(x0 * scale),
                    top=round(y0 * scale),
                    right=round(x1 * scale),
                    bottom=round(y1 * scale),
                    confidence=99.0,
                    line_key=(int(block_no), int(line_no)),
                )
            )
        full_text = normalize_text(page.get_text("text", sort=True))
    return image, words, full_text


def text_layer_is_usable(words: List[OCRWord], full_text: str) -> bool:
    alpha_numeric = sum(char.isalnum() for char in full_text)
    epic_hits = len(
        re.findall(r"\b[A-Z]{2,4}[\s\-]*\d{6,10}\b", full_text, flags=re.IGNORECASE)
    )
    label_hits = sum(
        len(re.findall(label, full_text, flags=re.IGNORECASE))
        for label in (r"नाम", r"आयु", r"लिंग", r"NAME", r"AGE", r"GENDER")
    )
    return len(words) >= 60 and alpha_numeric >= 180 and (epic_hits >= 2 or label_hits >= 6)
