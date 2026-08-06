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
    from backend.accuracy_patch import valid_epic
    from backend.config import ConversionConfig
    from backend.ocr import validate_ocr_languages

    validate_ocr_languages("hin+eng")
    if ConversionConfig().mode != "turbo" or ConversionConfig().settings.dpi != 250:
        raise RuntimeError("Strict reader configuration self-test failed.")

    epic = parser._extract_epic("क्रम 123 UCC0700005")
    if epic != "UCC0700005" or not valid_epic(epic):
        raise RuntimeError("Modern EPIC parser self-test failed: {}".format(epic))

    legacy = parser._extract_epic("UP/57/277/0036003")
    if legacy != "UP/57/277/0036003" or not valid_epic(legacy):
        raise RuntimeError("Legacy EPIC parser self-test failed: {}".format(legacy))

    noisy = parser._extract_epic("UCC3594744 7")
    if noisy != "UCC3594744":
        raise RuntimeError("EPIC boundary repair self-test failed: {}".format(noisy))

    if parser._extract_epic("ABC01234567"):
        raise RuntimeError("Ambiguous extra EPIC digit was incorrectly truncated.")

    serial = parser._extract_serial(["७६७ UCC0700005"])
    if serial != "767":
        raise RuntimeError("Serial parser self-test failed: {}".format(serial))


def main() -> None:
    if "--self-test" in sys.argv:
        self_test()
        return

    try:
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
