from __future__ import annotations

import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Iterable, List, Sequence


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = value.replace("|", " ").replace("¦", " ")
    value = re.sub(r"[\u200b-\u200f\u202a-\u202e\ufeff]", "", value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def clean_field(value: str, limit: int = 100) -> str:
    value = normalize_text(value)
    value = re.sub(r"^[\s:;,.\-–—]+|[\s:;,.\-–—]+$", "", value)
    return value[:limit].strip()


def safe_filename(value: str) -> str:
    base = Path(value or "file.pdf").name
    base = re.sub(r"[^A-Za-z0-9._\-]+", "_", base)
    return base[:160] or "file.pdf"


def cluster_positions(values: Sequence[int], tolerance: int) -> List[int]:
    if len(values) == 0:
        return []
    clusters: List[List[int]] = []
    for value in sorted(int(v) for v in values):
        if not clusters or value - clusters[-1][-1] > tolerance:
            clusters.append([value])
        else:
            clusters[-1].append(value)
    return [round(sum(cluster) / len(cluster)) for cluster in clusters]


def most_common_nonempty(values: Iterable[str]) -> str:
    cleaned = [clean_field(v) for v in values if clean_field(v)]
    if not cleaned:
        return ""
    counts = Counter(cleaned)
    return counts.most_common(1)[0][0]
