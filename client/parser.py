from pyside_show_message import show_message
from pyside_show_image import show_image
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

_SESSION_RUNNER = None

def set_session_runner(runner):
    global _SESSION_RUNNER
    _SESSION_RUNNER = runner

def parse_command(data):
    match data["type"]:
        case "show_message":
            title = data.get("title")
            body = data.get("body")
            
            lifespan_s = data.get("lifespan_s")
            if lifespan_s is None:
                lifespan_s = data.get("timer_s")
            if lifespan_s is None:
                lifespan_s = 10
            show_message(title, body, lifespan_s=lifespan_s)
        case "open_url":
            url = data.get("body") or ""
            url = url.strip()
            if not url:
                raise ValueError("open_url missing body")

            ok = QDesktopServices.openUrl(QUrl(url))
            if not ok:
                raise RuntimeError(f"Failed to open URL: {url}")
        case "image_popup":
            url = data.get("body")
            show_image(url)
        case "session_start":
            if _SESSION_RUNNER is None:
                raise RuntimeError("Session runner not configured")
            session_id = data.get("session_id") or "sess_unknown"
            steps = data.get("body")
            if not isinstance(steps, list):
                raise ValueError("session_start body must be a list")
            _SESSION_RUNNER.start(session_id, steps)

        case _:
            print(f"Unknown command type: {data.get('type')}")
