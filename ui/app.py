"""Native desktop window for the bot-suite control panel.

Reuses the existing local web panel (ui/web.py) but renders it inside a native
OS window via pywebview (uses the system webview — NOT a bundled Chromium like
Electron, so it stays light). Falls back to a clear message if pywebview is
missing.

Run it with:  ./bot app
"""
from __future__ import annotations

import threading
import time

from ui.web import run_server


def launch(host: str = "127.0.0.1", port: int = 8787,
           width: int = 1200, height: int = 820) -> None:
    try:
        import webview  # pywebview
    except ImportError:
        print("Native window needs pywebview. Install it:")
        print("    ./venv/bin/pip install pywebview")
        print("…or just use the browser panel:  ./bot web")
        return

    # Serve the panel locally in a background thread (daemon = dies with us).
    threading.Thread(
        target=run_server, kwargs={"host": host, "port": port}, daemon=True
    ).start()
    time.sleep(0.8)  # let the server bind before the window loads it

    webview.create_window(
        "bot-suite", f"http://{host}:{port}",
        width=width, height=height, min_size=(900, 600),
    )
    webview.start()  # blocks until the window is closed


if __name__ == "__main__":
    launch()
