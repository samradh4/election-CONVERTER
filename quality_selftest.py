from __future__ import annotations

import cv2
import numpy as np

import app  # applies the production accuracy patch
from backend import layout, parser
from backend.accuracy_patch import valid_epic
from backend.config import ConversionConfig
from backend.models import VoterRecord


def test_top_row_layout() -> None:
    height, width = 3500, 2480
    image = np.full((height, width, 3), 255, dtype=np.uint8)
    left, right = 40, width - 40
    top, bottom = 110, 3010  # starts near 3% of the page, like the client PDF
    x_edges = [round(left + (right - left) * index / 3) for index in range(4)]
    y_edges = [round(top + (bottom - top) * index / 10) for index in range(11)]
    for x in x_edges:
        cv2.line(image, (x, top), (x, bottom), (0, 0, 0), 4)
    for y in y_edges:
        cv2.line(image, (left, y), (right, y), (0, 0, 0), 4)
    boxes = layout._contour_boxes(image)
    assert len(boxes) == 30, "Top-row card detection failed: {}".format(len(boxes))
    assert min(box[1] for box in boxes) < round(height * 0.05)


def test_strict_epic() -> None:
    assert parser._extract_epic("क्रम 123 UCC0700005") == "UCC0700005"
    assert valid_epic("UCC0700005")
    assert parser._extract_epic("ABC01234567") == ""
    assert parser._extract_epic("AB1234567") == ""


def test_serial_guard() -> None:
    records = [VoterRecord(source_page=3, source_card=index + 1) for index in range(100)]
    records[0].serial_number = "1"
    records[10].serial_number = "11"
    parser.reconcile_serials(records)
    assert records[1].serial_number == "", "Weak two-anchor sequence must not overwrite rows"

    for index in range(20):
        records[index].serial_number = str(index + 1)
    parser.reconcile_serials(records)
    assert records[99].serial_number == "100", "Strong sequence evidence should repair a gap"


def main() -> None:
    assert ConversionConfig().mode == "turbo"
    assert ConversionConfig().settings.dpi == 250
    test_top_row_layout()
    test_strict_epic()
    test_serial_guard()
    print("Strict quality reader self-test passed")


if __name__ == "__main__":
    main()
