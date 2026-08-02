from __future__ import annotations

import argparse
from pathlib import Path

from backend.config import ConversionConfig
from backend.converter import convert_pdf
from backend.excel_writer import write_excel


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert an election PDF to Excel.")
    parser.add_argument("pdf", help="Input PDF path")
    parser.add_argument("-o", "--output", help="Output XLSX path")
    parser.add_argument("--mode", choices=("fast", "balanced", "accurate"), default="accurate")
    parser.add_argument("--constituency", default="")
    parser.add_argument("--section", default="")
    parser.add_argument("--part", default="")
    parser.add_argument("--manual-metadata", action="store_true")
    args = parser.parse_args()

    pdf_path = Path(args.pdf).expanduser().resolve()
    if not pdf_path.is_file():
        raise SystemExit("PDF not found: {}".format(pdf_path))
    output = Path(args.output).expanduser().resolve() if args.output else pdf_path.with_name(pdf_path.stem + "_converted.xlsx")
    config = ConversionConfig(
        mode=args.mode,
        use_manual_metadata=args.manual_metadata,
        constituency_override=args.constituency,
        section_override=args.section,
        part_override=args.part,
    )

    def progress(done: int, total: int, message: str) -> None:
        print("[{}/{}] {}".format(done, total, message), flush=True)

    result = convert_pdf(pdf_path, config=config, progress_callback=progress)
    write_excel(result, output)
    print("Created: {}".format(output))
    print("Records: {} | Review: {}".format(len(result.records), result.review_count))


if __name__ == "__main__":
    main()
