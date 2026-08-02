from __future__ import annotations

import itertools
import re
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np

from .models import OCRWord
from .utils import cluster_positions

Box = Tuple[int, int, int, int]


def _iou(first: Box, second: Box) -> float:
    ax, ay, aw, ah = first
    bx, by, bw, bh = second
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    if intersection <= 0:
        return 0.0
    union = aw * ah + bw * bh - intersection
    return intersection / union if union else 0.0


def _dedupe_boxes(boxes: Sequence[Box]) -> List[Box]:
    kept: List[Box] = []
    for box in sorted(boxes, key=lambda value: value[2] * value[3], reverse=True):
        if any(_iou(box, existing) > 0.82 for existing in kept):
            continue
        kept.append(box)
    return kept


def _grid_masks(image: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY) if image.ndim == 3 else image
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 35, 11
    )
    height, width = gray.shape
    horizontal_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (max(35, width // 34), 1)
    )
    vertical_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (1, max(30, height // 55))
    )
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horizontal_kernel)
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vertical_kernel)
    return binary, horizontal, vertical


def _contour_boxes(image: np.ndarray) -> List[Box]:
    height, width = image.shape[:2]
    _, horizontal, vertical = _grid_masks(image)
    grid = cv2.bitwise_or(horizontal, vertical)
    grid = cv2.dilate(grid, np.ones((3, 3), np.uint8), iterations=1)
    contours, _ = cv2.findContours(grid, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    boxes: List[Box] = []
    for contour in contours:
        x, y, box_width, box_height = cv2.boundingRect(contour)
        wr = box_width / float(width)
        hr = box_height / float(height)
        if not (0.23 <= wr <= 0.37 and 0.045 <= hr <= 0.14):
            continue
        if y < height * 0.07 or y + box_height > height * 0.98:
            continue
        boxes.append((x, y, box_width, box_height))
    return _dedupe_boxes(boxes)


def _choose_verticals(positions: List[int], width: int) -> List[int]:
    candidates = [p for p in positions if 0 <= p <= width]
    if len(candidates) < 4:
        return []
    best = None
    for combo in itertools.combinations(candidates, 4):
        gaps = [combo[index + 1] - combo[index] for index in range(3)]
        span = combo[-1] - combo[0]
        if span < width * 0.76:
            continue
        if min(gaps) < width * 0.22 or max(gaps) > width * 0.42:
            continue
        mean_gap = sum(gaps) / 3.0
        score = sum(abs(gap - mean_gap) for gap in gaps) + abs(span - width * 0.95)
        if best is None or score < best[0]:
            best = (score, list(combo))
    return best[1] if best else []


def _projection_boxes(image: np.ndarray) -> List[Box]:
    height, width = image.shape[:2]
    _, horizontal, vertical = _grid_masks(image)
    vertical_strength = np.count_nonzero(vertical, axis=0)
    horizontal_strength = np.count_nonzero(horizontal, axis=1)
    xs = cluster_positions(
        np.where(vertical_strength > height * 0.19)[0], max(3, width // 600)
    )
    ys = cluster_positions(
        np.where(horizontal_strength > width * 0.48)[0], max(3, height // 900)
    )
    verticals = _choose_verticals(xs, width)
    if len(verticals) != 4:
        return []
    intervals = []
    for top, bottom in zip(ys, ys[1:]):
        gap = bottom - top
        if height * 0.045 <= gap <= height * 0.14 and top > height * 0.06:
            intervals.append((top, bottom))
    boxes = []
    for top, bottom in intervals:
        for left, right in zip(verticals, verticals[1:]):
            boxes.append((left, top, right - left, bottom - top))
    return boxes


def _fixed_grid(image: np.ndarray) -> List[Box]:
    height, width = image.shape[:2]
    _, horizontal, _ = _grid_masks(image)
    strength = np.count_nonzero(horizontal, axis=1)
    ys = cluster_positions(
        np.where(strength > width * 0.50)[0], max(3, height // 900)
    )
    ys = [value for value in ys if height * 0.08 < value < height * 0.98]
    usable = []
    for value in ys:
        if not usable or value - usable[-1] > height * 0.025:
            usable.append(value)
    best_sequence: List[int] = []
    for start in range(len(usable)):
        sequence = [usable[start]]
        for value in usable[start + 1 :]:
            gap = value - sequence[-1]
            if height * 0.045 <= gap <= height * 0.125:
                sequence.append(value)
            elif gap > height * 0.125:
                break
        if len(sequence) > len(best_sequence):
            best_sequence = sequence
    if 5 <= len(best_sequence) <= 13:
        y_edges = best_sequence
    else:
        top, bottom = round(height * 0.17), round(height * 0.94)
        y_edges = [round(top + (bottom - top) * index / 10) for index in range(11)]
    left, right = round(width * 0.018), round(width * 0.982)
    x_edges = [round(left + (right - left) * index / 3) for index in range(4)]
    return [
        (
            x_edges[column],
            y_edges[row],
            x_edges[column + 1] - x_edges[column],
            y_edges[row + 1] - y_edges[row],
        )
        for row in range(len(y_edges) - 1)
        for column in range(3)
    ]


def _regularize_grid(boxes: Sequence[Box], image_shape: Tuple[int, ...]) -> List[Box]:
    if len(boxes) < 9:
        return list(boxes)
    height, width = image_shape[:2]
    centers_x = [round(box[0] + box[2] / 2.0) for box in boxes]
    centers_y = [round(box[1] + box[3] / 2.0) for box in boxes]
    median_w = float(np.median([box[2] for box in boxes]))
    median_h = float(np.median([box[3] for box in boxes]))
    x_clusters = cluster_positions(centers_x, max(20, round(median_w * 0.45)))
    y_clusters = cluster_positions(centers_y, max(14, round(median_h * 0.45)))
    if len(x_clusters) != 3 or not (3 <= len(y_clusters) <= 12):
        return list(boxes)
    x_clusters.sort()
    y_clusters.sort()
    x_edges = [max(0, round(x_clusters[0] - median_w / 2.0))]
    x_edges.extend(round((a + b) / 2.0) for a, b in zip(x_clusters, x_clusters[1:]))
    x_edges.append(min(width, round(x_clusters[-1] + median_w / 2.0)))
    y_edges = [max(0, round(y_clusters[0] - median_h / 2.0))]
    y_edges.extend(round((a + b) / 2.0) for a, b in zip(y_clusters, y_clusters[1:]))
    y_edges.append(min(height, round(y_clusters[-1] + median_h / 2.0)))
    return [
        (
            x_edges[column],
            y_edges[row],
            x_edges[column + 1] - x_edges[column],
            y_edges[row + 1] - y_edges[row],
        )
        for row in range(len(y_clusters))
        for column in range(3)
    ]


def _sort_boxes(boxes: Sequence[Box]) -> List[Box]:
    if not boxes:
        return []
    median_height = float(np.median([box[3] for box in boxes]))
    tolerance = max(8.0, median_height * 0.42)
    rows: List[List[Box]] = []
    row_centers: List[float] = []
    for box in sorted(boxes, key=lambda item: item[1] + item[3] / 2.0):
        center_y = box[1] + box[3] / 2.0
        target = -1
        distance = float("inf")
        for index, row_center in enumerate(row_centers):
            current = abs(center_y - row_center)
            if current <= tolerance and current < distance:
                target, distance = index, current
        if target < 0:
            rows.append([box])
            row_centers.append(center_y)
        else:
            rows[target].append(box)
            row_centers[target] = sum(
                value[1] + value[3] / 2.0 for value in rows[target]
            ) / len(rows[target])
    ordered: List[Box] = []
    for _, row in sorted(zip(row_centers, rows), key=lambda pair: pair[0]):
        ordered.extend(sorted(row, key=lambda item: item[0]))
    return ordered


def page_looks_like_voter_page(text: str, words: Sequence[OCRWord], box_count: int) -> bool:
    epic_hits = len(
        re.findall(r"\b[A-Z]{2,4}[\s\-]*\d{6,10}\b", text, flags=re.IGNORECASE)
    )
    label_hits = sum(
        len(re.findall(label, text, flags=re.IGNORECASE))
        for label in (r"नाम", r"आयु", r"लिंग", r"NAME", r"AGE", r"GENDER")
    )
    if box_count >= 9:
        return True
    if box_count in (3, 6) and (epic_hits >= 1 or label_hits >= 3):
        return True
    return epic_hits >= 3 or label_hits >= 12


def detect_voter_boxes(
    image: np.ndarray, words: Optional[Sequence[OCRWord]] = None, text: str = ""
) -> Tuple[List[Box], str, bool]:
    contour = _contour_boxes(image)
    projection = _projection_boxes(image)
    candidates = []
    if 3 <= len(contour) <= 45:
        candidates.append((contour, "contours"))
    if 3 <= len(projection) <= 45:
        candidates.append((projection, "projection"))
    best_boxes: List[Box] = []
    method = "none"
    if candidates:
        candidates.sort(key=lambda item: (abs((len(item[0]) % 3)), -len(item[0])))
        best_boxes, method = candidates[0]
        best_boxes = _regularize_grid(best_boxes, image.shape)
    voter_page = page_looks_like_voter_page(text, words or [], len(best_boxes))
    if voter_page and len(best_boxes) < 3:
        best_boxes = _fixed_grid(image)
        method = "fixed-grid"
    if voter_page and len(best_boxes) % 3 != 0:
        regularized = _regularize_grid(best_boxes, image.shape)
        if len(regularized) >= len(best_boxes):
            best_boxes = regularized
    return _sort_boxes(best_boxes), method, voter_page


def crop_box(image: np.ndarray, box: Box, padding: int = 3) -> np.ndarray:
    x, y, width, height = box
    image_height, image_width = image.shape[:2]
    x1 = max(0, x - padding)
    y1 = max(0, y - padding)
    x2 = min(image_width, x + width + padding)
    y2 = min(image_height, y + height + padding)
    return image[y1:y2, x1:x2]


def estimate_photo_available(image: np.ndarray, box: Box) -> bool:
    crop = crop_box(image, box, 0)
    if crop.size == 0:
        return False
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY) if crop.ndim == 3 else crop
    region = gray[:, int(gray.shape[1] * 0.73) :]
    if region.size == 0:
        return False
    return float(np.mean(region < 235)) > 0.065 and float(np.std(region)) > 16.0
