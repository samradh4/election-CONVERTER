from __future__ import annotations

import ctypes
import socket
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


def main() -> None:
    try:
        port = free_port()
        url = "http://127.0.0.1:{}".format(port)

        def open_browser() -> None:
            time.sleep(1.2)
            webbrowser.open(url, new=1)

        threading.Thread(target=open_browser, daemon=True).start()

        # The application is built as a windowed EXE, so sys.stdout and
        # sys.stderr are unavailable. Uvicorn's default logging formatter tries
        # to attach to those streams and crashes with:
        # "Unable to configure formatter 'default'".
        # Disable Uvicorn's dictConfig logging and run silently instead.
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
