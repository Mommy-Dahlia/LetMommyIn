from pyside_show_message import show_message
from pyside_show_image import show_image
from pyside_overlay import show_gif_overlay, stop_gif_overlays
from audio_manager import AudioManager
from subliminal_manager import SubliminalManager
from wfm_manager import WfmManager
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices


_SESSION_RUNNER = None

def set_session_runner(runner):
    global _SESSION_RUNNER
    _SESSION_RUNNER = runner
    
_AUDIO_MANAGER = None

def set_audio_manager(mgr: AudioManager):
    global _AUDIO_MANAGER
    _AUDIO_MANAGER = mgr
    
_SUBLIMINAL_MANAGER = None

def set_subliminal_manager(mgr: SubliminalManager):
    global _SUBLIMINAL_MANAGER
    _SUBLIMINAL_MANAGER = mgr

_WFM_MANAGER = None

def set_wfm_manager(mgr: WfmManager):
    global _WFM_MANAGER
    _WFM_MANAGER = mgr
    
_ACK_QUEUE = None

def set_ack_queue(q):
    global _ACK_QUEUE
    _ACK_QUEUE = q

def parse_command(data):
    if not isinstance(data, dict):
        raise ValueError("Command must be a dict")
    if "type" not in data:
        raise ValueError("Command missing type")
    match data["type"]:
        case "show_message":
            title = data.get("title")
            body = data.get("body")
            
            lifespan_s = data.get("lifespan_s")
            if lifespan_s is None:
                lifespan_s = data.get("timer_s")
            if lifespan_s is None:
                lifespan_s = 8
            try:
                lifespan_s = float(lifespan_s)
            except Exception:
                lifespan_s = 8.0
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
        case "audio_play":
            if _AUDIO_MANAGER is None:
                raise RuntimeError("Audio manager not configured")

            url = data.get("url") or data.get("body")  # allow either
            volume = data.get("volume", 0.8)
            loop = bool(data.get("loop", True))
            duration_s = data.get("duration_s")  # optional

            _AUDIO_MANAGER.play(url=url, volume=volume, loop=loop, duration_s=duration_s)

        case "audio_stop":
            if _AUDIO_MANAGER is None:
                raise RuntimeError("Audio manager not configured")
            _AUDIO_MANAGER.stop()
        case "subliminal_start":
            if _SUBLIMINAL_MANAGER is None:
                raise RuntimeError("Subliminal manager not configured")
            messages = data.get("messages")
            duration_s = data.get("duration_s", None)
            interval_ms = data.get("interval_ms", 2000)
            flash_ms = data.get("flash_ms", 40)
            font_pt = data.get("font_pt", 40)
            _SUBLIMINAL_MANAGER.start(
                messages,
                duration_s=duration_s,
                interval_ms=interval_ms,
                flash_ms=flash_ms,
                font_pt=font_pt,
            )
        case "subliminal_stop":
            if _SUBLIMINAL_MANAGER is None:
                raise RuntimeError("Subliminal manager not configured")
            _SUBLIMINAL_MANAGER.stop()
        case "session_start":
            if _SESSION_RUNNER is None:
                raise RuntimeError("Session runner not configured")
            session_id = data.get("session_id") or "sess_unknown"
            steps = data.get("body")
            if not isinstance(steps, list):
                raise ValueError("session_start body must be a list")
            _SESSION_RUNNER.start(session_id, steps)
        case "write_for_me":
            if _SESSION_RUNNER is None:
                raise RuntimeError("Session runner not configured")
            if _WFM_MANAGER is None:
                raise RuntimeError("WFM manager not configured")

            text = data.get("text") or data.get("body") or ""
            reps = data.get("reps") or data.get("targetreps") or 5
            cmd_id = data.get("id") or "-"

            # Pause session, resume only when done
            _SESSION_RUNNER.pause()
            def _done():
                _SESSION_RUNNER.resume()
                # send completion ack back to server
                if _ACK_QUEUE is not None and cmd_id and cmd_id != "-":
                    try:
                        _ACK_QUEUE.put_nowait({
                            "id": cmd_id,
                            "status": "wfm_completed",
                            "detail": f"reps={int(reps)}",
                        })
                    except Exception:
                        # if queue is full or missing, don't block UI
                        pass

            _WFM_MANAGER.start(text=str(text), reps=int(reps), on_done=_done)
        case "gif_overlay":
            url= data.get("body") or data.get("url") or ""
            opacity = data.get("opacity", 0.4)
            screen = data.get("screen", -1)  # -1 = all
            show_gif_overlay(url, screen=int(screen), opacity=float(opacity))
        case "gif_overlay_stop":
            stop_gif_overlays()
        case _:
            print(f"Unknown command type: {data.get('type')}")
