import threading
import time
import urllib.request
import webbrowser
import uvicorn
from app.config import HOST, PORT


def open_browser():
    """Polls the local server until it responds, then launches the browser."""
    url = f"http://{HOST}:{PORT}"
    for _ in range(30):
        time.sleep(0.3)
        try:
            with urllib.request.urlopen(url, timeout=1):
                break
        except Exception:
            continue
    webbrowser.open(url)


if __name__ == "__main__":
    # Start browser-opener thread
    threading.Thread(target=open_browser, daemon=True).start()

    # Launch ASGI web server
    uvicorn.run(
        "app.web:app",
        host=HOST,
        port=PORT,
        log_level="info",
        reload=False,  # False avoids subprocess fork collision on desktop runners
    )