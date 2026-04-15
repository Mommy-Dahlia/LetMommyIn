from pyside_show_message import show_message
from pyside_show_image import show_image
from pyside_overlay import show_gif_overlay, stop_gif_overlays
from audio_manager import AudioManager
from subliminal_manager import SubliminalManager
from wfm_manager import WfmManager
from ui_settings import get_popup_screens, get_default_audio_url, get_default_overlay, get_popup_sfx_path, get_session_receive_mode
from pyside_session_warning import run_session_warning_dialog
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtMultimedia import QSoundEffect
from session_runner import _apply_pns
import os

def _popup_delay():
    from time import sleep
    sleep(0.25)  # 80ms buffer

_SESSION_RUNNER = None

def set_session_runner(runner):
    global _SESSION_RUNNER
    _SESSION_RUNNER = runner
    
_INJECTION_HANDLER = None

def set_injection_handler(fn):
    global _INJECTION_HANDLER
    _INJECTION_HANDLER = fn
    
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
    
_SFX = None

def _get_sfx() -> QSoundEffect | None:
    global _SFX
    if _SFX is None:
        _SFX = QSoundEffect()
        _SFX.setVolume(0.6)  # tweak as desired
    return _SFX

def play_popup_sfx() -> None:
    path = get_popup_sfx_path()
    if not path:
        return

    # Ensure local file exists; skip silently if missing
    if not os.path.exists(path):
        return

    sfx = _get_sfx()
    if not sfx:
        return

    # Re-set source each time so changing settings works immediately
    sfx.setSource(QUrl.fromLocalFile(path))
    sfx.play()
    
def _apply_client_session_defaults(steps: list[dict]) -> list[dict]:
    """
    Client-side policy applied at session_start.

    - If default_overlay_url is set and the session has no gif_overlay/gif_overlay_stop,
      inject a gif_overlay at the beginning and ensure a stop exists at the end.

    - If default_audio_url is set:
        * If the session already has audio_play steps, override their url.
        * Else inject an audio_play at the beginning.
      Also ensure an audio_stop exists at the end.
    """
    out = [dict(s) if isinstance(s, dict) else s for s in steps]

    # ---- Overlay default ----
    overlay_url, overlay_opacity, overlay_screen = get_default_overlay()
    if overlay_url:
        has_overlay_any = any(s.get("type") in ("gif_overlay", "gif_overlay_stop") for s in out if isinstance(s, dict))
        if not has_overlay_any:
            out.insert(0, {
                "type": "gif_overlay",
                "url": overlay_url,
                "body": overlay_url,
                "opacity": overlay_opacity,
                "screen": overlay_screen,
                "timer_s": 0,  # start immediately; don't consume pacing
            })

        has_overlay_stop = any(s.get("type") == "gif_overlay_stop" for s in out if isinstance(s, dict))
        if not has_overlay_stop:
            out.append({"type": "gif_overlay_stop", "timer_s": 0})

    # ---- Audio default ----
    default_audio = get_default_audio_url()
    if default_audio:
        saw_audio_play = False
        for s in out:
            if isinstance(s, dict) and s.get("type") == "audio_play":
                saw_audio_play = True

        if not saw_audio_play:
            out.insert(0, {
                "type": "audio_play",
                "url": default_audio,
                "volume": 0.8,
                "loop": True,
                "timer_s": 0,
                "_is_default": True,  # marker
            })

        has_audio_stop = any(s.get("type") == "audio_stop" for s in out if isinstance(s, dict))
        if not has_audio_stop:
            out.append({"type": "audio_stop", "timer_s": 0})

    return out

def parse_command(data):
    if not isinstance(data, dict):
        raise ValueError("Command must be a dict")
    if "type" not in data:
        raise ValueError("Command missing type")
    match data["type"]:
        case "inject_block":
            if _INJECTION_HANDLER is None:
                raise RuntimeError("Injection handler not configured")
            _INJECTION_HANDLER(data)
            return

        case "inject_session":
            if _INJECTION_HANDLER is None:
                raise RuntimeError("Injection handler not configured")
            _INJECTION_HANDLER(data)
            return
        
        case "inject_behavior":
            if _INJECTION_HANDLER is None:
                raise RuntimeError("Injection handler not configured")
            _INJECTION_HANDLER(data)
            return
        
        case "show_message":
            title = data.get("title")
            body = data.get("body")
            
            if title:
                title = _apply_pns(str(title))
            if body:
                body = _apply_pns(str(body))
            
            lifespan_s = data.get("lifespan_s")
            if lifespan_s is None:
                lifespan_s = data.get("timer_s")
            if lifespan_s is None:
                lifespan_s = 8
            try:
                lifespan_s = float(lifespan_s)
            except Exception:
                lifespan_s = 8.0
            play_popup_sfx()
            show_message(title, body, lifespan_s=lifespan_s)
            _popup_delay()
        case "open_url":
            url = data.get("body") or ""
            url = url.strip()
            if not url:
                raise ValueError("open_url missing body")

            play_popup_sfx()
            ok = QDesktopServices.openUrl(QUrl(url))
            if not ok:
                raise RuntimeError(f"Failed to open URL: {url}")
            _popup_delay()
        case "image_popup":
            url = data.get("body") or data.get("url")
            play_popup_sfx()
            show_image(url)
            _popup_delay()
        case "audio_play":
            if _AUDIO_MANAGER is None:
                raise RuntimeError("Audio manager not configured")

            url = data.get("url") or data.get("body")  # allow either
            volume = data.get("volume", 0.8)
            loop = bool(data.get("loop", True))
            duration_s = data.get("duration_s")  # optional

            _AUDIO_MANAGER.play(url=url, volume=volume, loop=loop, duration_s=duration_s)
            _popup_delay()

        case "audio_stop":
            if _AUDIO_MANAGER is None:
                raise RuntimeError("Audio manager not configured")
            _AUDIO_MANAGER.stop()
            _popup_delay()
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
            _popup_delay()
        case "subliminal_stop":
            if _SUBLIMINAL_MANAGER is None:
                raise RuntimeError("Subliminal manager not configured")
            _SUBLIMINAL_MANAGER.stop()
            _popup_delay()
        case "session_start":
            if _SESSION_RUNNER is None:
                raise RuntimeError("Session runner not configured")

            session_id = data.get("session_id") or "sess_unknown"

            steps = data.get("body")
            if not isinstance(steps, list):
                raise ValueError("session_start body must be a list")

            # ---- new: gate based on user preference ----
            mode = get_session_receive_mode()  # "full" | "minimal" | "off"

            title = str(data.get("title") or "").strip()
            summary = str(data.get("summary") or "").strip()
            intensity = data.get("intensity", None)
            tags = data.get("tags") or []
            blocks = data.get("blocks") or []

            if mode != "off":
                accepted = run_session_warning_dialog(
                    mode=mode,
                    title=title,
                    summary=summary,
                    intensity=intensity,
                    tags=[str(t) for t in tags if str(t).strip()],
                    blocks=[str(b) for b in blocks if str(b).strip()],
                )
                if not accepted:
                    # Optional: if you want server visibility, push an ack
                    cmd_id = data.get("id") or "-"
                    if _ACK_QUEUE is not None and cmd_id and cmd_id != "-":
                        try:
                            _ACK_QUEUE.put_nowait({"id": cmd_id, "status": "declined", "detail": "user declined session_start"})
                        except Exception:
                            pass
                    return

            # Apply defaults only if we're actually starting
            steps = _apply_client_session_defaults(steps)
            _SESSION_RUNNER.start(session_id, steps)
            _popup_delay()
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

            play_popup_sfx()
            _WFM_MANAGER.start(text=str(text), reps=int(reps), on_done=_done)
            _popup_delay()
        case "gif_overlay":
            url= data.get("body") or data.get("url") or ""
            opacity = data.get("opacity", 0.4)
            screen = data.get("screen", None)  # -1 = all
            if screen is None:
                sel = get_popup_screens()
                if sel is None:
                    screen = -1
                elif len(sel) == 1:
                    screen = sel[0]
                else:
                    # overlay on all selected screens: loop and call show_gif_overlay per screen index
                    for idx in sel:
                        show_gif_overlay(url, screen=int(idx), opacity=float(opacity))
                    return
            show_gif_overlay(url, screen=int(screen), opacity=float(opacity))
            _popup_delay()
        case "gif_overlay_stop":
            stop_gif_overlays()
            _popup_delay()
        case _:
            print(f"Unknown command type: {data.get('type')}")
