from __future__ import annotations

from typing import Iterable, List, Sequence, Tuple

import numpy as np

from .models import OCRWord
from .ocr import image_to_words

Box = Tuple[int, int, int, int]


def _offset_words(words: Iterable[OCRWord], x_offset: int, key_prefix: int) -> List[OCRWord]:
    shifted: List[OCRWord] = []
    for word in words:
        line_key = (key_prefix,) + tuple(word.line_key or ())
        shifted.append(
            OCRWord(
                text=word.text,
                left=word.left + x_offset,
                top=word.top,
                right=word.right + x_offset,
                bottom=word.bottom,
                confidence=word.confidence,
                line_key=line_key,
            )
        )
    return shifted


def read_page_columns(
    image: np.ndarray,
    languages: str,
    columns: int = 3,
    psm: int = 6,
) -> List[OCRWord]:
    """Read a voter page in a few large batches instead of one OCR call per card.

    Electoral rolls normally use three columns. Reading each complete column with
    stronger preprocessing keeps the text large enough for Hindi OCR while
    limiting a page to three repair calls rather than 20-30 card calls.
    """
    if image.size == 0:
        return []
    width = image.shape[1]
    columns = max(1, int(columns))
    result: List[OCRWord] = []
    for column in range(columns):
        x1 = round(width * column / columns)
        x2 = round(width * (column + 1) / columns)
        crop = image[:, x1:x2]
        words = image_to_words(crop, languages, psm=psm, strong=True)
        result.extend(_offset_words(words, x1, column + 1))
    return result


def read_card_top_lines(
    image: np.ndarray,
    boxes: Sequence[Box],
    languages: str,
    psm: int = 11,
) -> List[OCRWord]:
    """Read all serial/EPIC strips in one sparse-page OCR pass."""
    if image.size == 0 or not boxes:
        return []
    canvas = np.full_like(image, 255)
    height, width = image.shape[:2]
    for x, y, box_width, box_height in boxes:
        x1 = max(0, x)
        y1 = max(0, y)
        x2 = min(width, x + box_width)
        y2 = min(height, y + max(1, round(box_height * 0.38)))
        if x2 > x1 and y2 > y1:
            canvas[y1:y2, x1:x2] = image[y1:y2, x1:x2]
    return image_to_words(canvas, languages, psm=psm, strong=True)
