from __future__ import annotations

import ctypes
import socket
import sys
import threading
import time
import webbrowser

import uvicorn

from app import app


def free_port(start: int = 8000, end: int = 8099) -> int:
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("No free local port found between 8000 and 8099.")


def show_error(message: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(0, message, "Election PDF Converter", 0x10)
    except Exception:
        pass


def self_test() -> None:
    """Validate the exact bundled OCR/parser runtime before publishing the EXE."""
    from backend import parser
    from backend.ocr import validate_ocr_languages

    validate_ocr_languages("hin+eng")
    epic = parser._extract_epic("123 ABC O1234567")
    if epic != "ABC01234567":
        raise RuntimeError("EPIC parser self-test failed: {}".format(epic))
    serial = parser._extract_serial(["७६७ ABC01234567"])
    if serial != "767":
        raise RuntimeError("Serial parser self-test failed: {}".format(serial))


def main() -> None:
    try:
        if "--self-test" in sys.argv:
            self_test()
            return

        port = free_port()
        url = "http://127.0.0.1:{}".format(port)

        def open_browser() -> None:
            time.sleep(1.2)
            webbrowser.open(url, new=1)

        threading.Thread(target=open_browser, daemon=True).start()

        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="critical",
            access_log=False,
            log_config=None,
        )
        server = uvicorn.Server(config)
        server.run()
    except Exception as exc:
        show_error("The converter could not start.\n\n{}".format(exc))
        raise


if __name__ == "__main__":
    main()
