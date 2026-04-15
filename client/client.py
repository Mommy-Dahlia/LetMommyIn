import json
import os
import sys
import socket
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
import asyncio
import argparse
import logging
from logging.handlers import RotatingFileHandler
import time
from urllib.parse import urlparse
import queue
import re

import websockets
import threading
from PySide6.QtCore import QObject, Signal, QTimer
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox, QDialog
from PySide6.QtGui import QIcon
if not sys.platform.startswith("linux"):
    from pynput import keyboard

from parser import parse_command, _apply_client_session_defaults, set_session_runner, set_audio_manager, set_subliminal_manager, set_wfm_manager, set_ack_queue, set_injection_handler
from pyside_show_message import close_all_messages
from pyside_show_image import close_all_images
from pyside_show_writeforme import close_all_wfm
from pyside_overlay import stop_gif_overlays
from session_runner import SessionRunner
from audio_manager import AudioManager
from subliminal_manager import SubliminalManager
from wfm_manager import WfmManager
from tray_manager import TrayManager
from ui_settings import (
    set_popup_screens, set_pet_names, set_default_audio_url, set_default_overlay,
    get_pet_names, get_default_audio_url, get_default_overlay, set_popup_sfx_path,
    set_image_save_enabled, set_image_save_dir, set_session_receive_mode
)
from session_compiler import SessionCompiler
from session_launcher import SessionLauncherDialog
from ui_theme import apply_app_theme
from pyside_injection_summary import InjectionBatchNotifier, InjectEvent
from behavior_manager import BehaviorManager, load_behaviors, save_behaviors
from behavior_settings_dialog import BehaviorSettingsDialog
from session_customizer import SessionCustomizerDialog

import ssl
import certifi
os.environ['SSL_CERT_FILE'] = certifi.where()
os.environ['REQUESTS_CA_BUNDLE'] = certifi.where()

_injection_notifier = InjectionBatchNotifier(quiet_ms=800)

_CODE_PATTERN = re.compile(r'^[A-Za-z0-9_\-]{16,}$')

def _looks_like_enroll_code(text: str) -> bool:
    return bool(_CODE_PATTERN.match(text.strip()))

async def _send_to_server(payload: dict) -> None:
    global _NET_WS
    ws = _NET_WS
    if ws is not None:
        try:
            await ws.send(json.dumps(payload))
        except Exception:
            pass

def get_content_roots(config_dir: Path) -> list[Path]:
    """
    Search order:
      1) local writable content (future server-downloaded sessions)
      2) bundled read-only content (ships with EXE)
    """
    local_root = config_dir / "content"
    bundled_root = (Path(sys._MEIPASS) / "content") if hasattr(sys, "_MEIPASS") else (Path(__file__).parent / "content")
    return [local_root, bundled_root]

_INVALID_FS_CHARS = r'<>:"/\\|?*\0'

def safe_stem(name: str) -> str:
    """
    Make a filesystem-safe stem. Keeps it readable, avoids Windows reserved chars.
    If your titles are already clean, this will usually return the same string.
    """
    s = (name or "").strip()
    s = "".join("_" if c in _INVALID_FS_CHARS else c for c in s)
    s = re.sub(r"\s+", " ", s).strip()
    # Windows also hates trailing dots/spaces
    s = s.rstrip(" .")
    return s or "untitled"

_NET_LOOP: asyncio.AbstractEventLoop | None = None
_shutdown_event: asyncio.Event | None = None
_NET_STOP = threading.Event()
_HOTKEY_LISTENER = None

def resource_path(relative: str) -> str:
    if hasattr(sys, "_MEIPASS"):
        return str(Path(sys._MEIPASS) / relative)
    return str(Path(__file__).parent / relative)

def icon_path(stem: str) -> str:
    """Returns platform-appropriate icon path."""
    ext = ".ico" if sys.platform.startswith("win") else ".png"
    return resource_path(f"{stem}{ext}")

if sys.platform.startswith("win"):
    CONFIG_DIR = Path(os.getenv("APPDATA", ".")) / "LMI"
else:
    CONFIG_DIR = Path.home() / ".config" / "LMI"
CONFIG_PATH = CONFIG_DIR / "config.json"

LOG_DIR = CONFIG_DIR / "logs"
LOG_PATH = LOG_DIR / "client.log"

ACK_RECEIVED = "received"
ACK_COMPLETED = "completed"
ACK_FAILED = "failed"

@dataclass
class ClientConfig:
    device_id: str
    username: str
    server_base_url: str          # e.g. "wss://lmi.<DOMAIN>.com/ws"
    device_token: str | None = None
    popup_screens: list[int] | None = None   # None = all screens
    audio_device_id: str | None = None       # None = default output
    pet_names: list[str] | None = None
    default_audio_url: str | None = None
    default_overlay_url: str | None = None
    default_overlay_opacity: float = 0.3
    default_overlay_screen: int = -1
    popup_sfx_path: str | None = None
    image_save_enabled: bool = True
    image_save_dir: str | None = None
    tier: str = "free"
    session_receive_mode: str = "full"  # "full" | "minimal" | "off"
    
MANIFEST_PATH = CONFIG_DIR / "catalogue_manifest.json"

def load_local_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        return {"sessions": {}, "blocks": {}, "behaviors": {}}
    try:
        with MANIFEST_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"sessions": {}, "blocks": {}, "behaviors": {}}

def save_local_manifest(manifest: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with MANIFEST_PATH.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

def update_manifest_entry(kind: str, title: str, updated_at: int) -> None:
    manifest = load_local_manifest()
    manifest[kind][title] = updated_at
    save_local_manifest(manifest)
    
def update_behavior_manifest_entry(behavior_type: str, name: str, updated_at: int) -> None:
    manifest = load_local_manifest()
    if "behaviors" not in manifest:
        manifest["behaviors"] = {}
    manifest["behaviors"][f"{behavior_type}:{name}"] = updated_at
    save_local_manifest(manifest)
    
def compute_wanted(server_catalogue: dict) -> tuple[list[str], list[str]]:
    manifest = load_local_manifest()
    local_sessions = manifest.get("sessions", {})
    local_blocks = manifest.get("blocks", {})
    local_behaviors = manifest.get("behaviors", {})

    want_sessions = [
        s["title"] for s in server_catalogue.get("sessions", [])
        if s["title"] not in local_sessions
        or local_sessions[s["title"]] < s["updated_at"]
    ]

    want_blocks = [
        b["title"] for b in server_catalogue.get("blocks", [])
        if b["title"] not in local_blocks
        or local_blocks[b["title"]] < b["updated_at"]
    ]
    
    want_behaviors = [
        (b["name"], b["behavior_type"])
        for b in server_catalogue.get("behaviors", [])
        if f"{b['behavior_type']}:{b['name']}" not in local_behaviors
        or local_behaviors[f"{b['behavior_type']}:{b['name']}"] < b["updated_at"]
    ]

    return want_sessions, want_blocks, want_behaviors

def load_config() -> ClientConfig | None:
    if not CONFIG_PATH.exists():
        return None

    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)

    # New format
    if "server_base_url" in data:
        cfg = ClientConfig(**data)

        # --- v0.2 normalization ---
        # Ensure tier is valid even if missing or malformed
        cfg.tier = (cfg.tier or "free").strip().lower()
        if cfg.tier not in ("free", "paid"):
            cfg.tier = "free"

        return cfg

    # Unknown config format
    return None

class CommandBridge(QObject):
    command_received = Signal(dict)
    toggle_pause = Signal()
    
class CommandDispatcher(QObject):
    def __init__(self, ack_queue: "queue.Queue[dict]"):
        super().__init__()
        self.ack_queue = ack_queue

    def handle_command(self, data: dict) -> None:
        t = data.get("type")
        if t == "__tier__":
            tier = (data.get("tier") or "free").strip().lower()

            # 1) persist to config
            # You need access to cfg here. Easiest pattern: attach cfg onto dispatcher at init.
            if hasattr(self, "cfg") and self.cfg is not None:
                self.cfg.tier = tier
                save_config(self.cfg)
                
            if hasattr(self, "behavior_manager") and self.behavior_manager is not None:
                if tier == "paid":
                    self.behavior_manager.start()
                else:
                    self.behavior_manager._general_timer.stop()
                    self.behavior_manager._autodrainer_timer.stop()

            # 2) update any in-memory UI settings (optional but useful)
            # If you don't want ui_settings to track tier yet, skip this.
            try:
                from ui_settings import set_entitlements  # if you add it
                set_entitlements(tier)
            except Exception:
                pass

            # 3) apply local feature gates to tray
            if hasattr(self, "tray") and self.tray is not None:
                self.tray.apply_feature_gates(tier)

            return
        if t == "__tray_status__":
            if hasattr(self, "tray") and self.tray is not None:
                self.tray.set_last_server_cmd_ts(data.get("last_command_ts"))
            return

        if t == "__tray_connected__":
            if hasattr(self, "tray") and self.tray is not None:
                self.tray.set_connected(bool(data.get("connected")))
            return
        
        if t == "__enroll_complete__":
            QMessageBox.information(
                None,
                "Welcome~",
                "You're all set up, darling~\n\n"
                "Mommy's app is now running in your system tray — "
                "look for the icon in the bottom right corner of your screen "
                "(you may need to click the little arrow to see it)~\n\n"
                "If you're not sure what to do next, check the FAQ on the server~",
            )
            return
        
        if t == "__session_started__":
            loop = _NET_LOOP
            ws = _NET_WS  # or use the shutdown event pattern
            if loop is not None:
                payload = {
                    "type": "session_started",
                    "session_id": data.get("session_id"),
                    "estimated_s": data.get("estimated_s"),
                    "started_at": data.get("started_at"),
                }
                asyncio.run_coroutine_threadsafe(
                    _send_to_server(payload),
                    loop
                )
            return

        cmd_id = data.get("id", "-")
        try:
            parse_command(data)
        except Exception as e:
            logging.exception(
                "Command failed in UI thread id=%s type=%s",
                cmd_id,
                data.get("type"),
            )
            try:
                self.ack_queue.put_nowait({
                    "id": cmd_id,
                    "status": "failed",
                    "detail": str(e),
                })
            except queue.Full:
                logging.warning("Ack queue full; dropping ack for id=%s", cmd_id)

def save_config(cfg: ClientConfig) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(asdict(cfg), f, indent=2)


def build_hello(cfg) -> dict:
    return {
        "type": "hello",
        "device_id": cfg.device_id,
        "username": cfg.username,
        "device_name": socket.gethostname(),
        "version": "v0.2",
        "protocol": "v0.2"
    }

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--server",
        help="Override server WebSocket URL (e.g. ws://127.0.0.1:8000/ws or wss://example.com/ws)",
        type=str,
    )
    return parser.parse_args()

async def send_ack(ws, command_id: str, status: str, detail: str = "") -> None:
    payload = {
        "type": "ack",
        "id": command_id,
        "status": status,
        "detail": detail,
    }
    await ws.send(json.dumps(payload))
    logging.info("Ack sent id=%s status=%s", command_id, status)
    
async def ack_sender_loop(ws, ack_queue: "queue.Queue[dict]") -> None:
    """
    Runs in the asyncio (network) thread. Waits for ack requests from the Qt thread and sends them.
    """
    while True:
        # Block waiting for the next ack request, without blocking the asyncio loop
        try:
            item = await asyncio.to_thread(ack_queue.get, timeout=5)
        except queue.Empty:
            # No ack pending; check if we should still be running
            continue
        except asyncio.CancelledError:
            return
        command_id = item.get("id", "-")
        status = item.get("status", "ack")
        detail = item.get("detail", "")
        try:
            await send_ack(ws, command_id, status=status, detail=detail)
        except Exception:
            return  # ws is gone; exit cleanly

async def heartbeat_loop(ws, interval_s: int = 25) -> None:
    while True:
        await asyncio.sleep(interval_s)
        try:
            payload = {"type": "heartbeat", "ts": time.time()}
            await ws.send(json.dumps(payload))
            logging.info("Heartbeat sent")
        except Exception as e:
            logging.warning("Heartbeat failed: %r", e)
            return

def prompt_enroll_code() -> str | None:
    code, ok = QInputDialog.getText(
        None,
        "Let Mommy In",
        "Type in Mommy's special passcode~  If you don't have one, message Mommy~",
    )

    if not ok:
        return None

    code = (code or "").strip()
    return code if code else None


def get_enroll_code_or_exit() -> str:
    code = prompt_enroll_code()
    if not code:
        logging.error("Enrollment cancelled by user.")
        raise SystemExit(1)
    return code

def write_injected_block(local_root: Path, *, title: str, summary: str, tags: list[str], intensity: int | None, body: str) -> str:
    blocks_dir = local_root / "blocks"
    blocks_dir.mkdir(parents=True, exist_ok=True)

    stem = safe_stem(title)
    (blocks_dir / f"{stem}.txt").write_text((body or "").replace("\r\n", "\n").replace("\r", "\n"), encoding="utf-8")

    meta = {
        "title": title,
        "summary": summary or "",
        "tags": tags or [],
        "intensity": intensity,
    }
    (blocks_dir / f"{stem}.meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return stem

def write_injected_session(local_root: Path, *, title: str, summary: str, tags: list[str], intensity: int | None, session_json: dict) -> str:
    sessions_dir = local_root / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    stem = safe_stem(title)

    # session file (what SessionCompiler reads)
    (sessions_dir / f"{stem}.json").write_text(json.dumps(session_json, ensure_ascii=False, indent=2), encoding="utf-8")

    # meta sidecar (what SessionLauncher shows)
    meta = {
        "title": title,
        "summary": summary or "",
        "tags": tags or [],
        "intensity": intensity,
    }
    (sessions_dir / f"{stem}.meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return stem

def write_injected_behavior(local_root: Path, *, behavior_type: str, name: str, entry: dict) -> None:
    out_dir = local_root / "behaviors"
    out_dir.mkdir(parents=True, exist_ok=True)
    pool_path = out_dir / f"{behavior_type}.json"

    # load existing pool
    if pool_path.exists():
        try:
            with pool_path.open("r", encoding="utf-8") as f:
                pool = json.load(f)
        except Exception:
            pool = []
    else:
        pool = []

    # find and replace existing entry with same name, or append
    entry_with_name = dict(entry)
    entry_with_name["_name"] = name

    existing_idx = next(
        (i for i, e in enumerate(pool) if e.get("_name") == name), None
    )
    if existing_idx is not None:
        pool[existing_idx] = entry_with_name
    else:
        pool.append(entry_with_name)

    with pool_path.open("w", encoding="utf-8") as f:
        json.dump(pool, f, indent=2, ensure_ascii=False)

def prompt_username() -> str:
    while True:
        username, ok = QInputDialog.getText(
            None,
            "Let Mommy In",
            "Which one of Mommy's sweeties is downloading this~?",
        )

        if not ok or not username.strip():
            QMessageBox.critical(
                None,
                "Setup cancelled",
                "I need to know who you are, darling~  Try again~",
            )
            raise SystemExit(1)
        
        if _looks_like_enroll_code(username.strip()):
            QMessageBox.warning(
                None,
                "Sweetheart...",
                "That looks like Mommy's passcode, not your name~\n\n"
                "Mommy asked for YOUR name, not the code~\n"
                "I know you're eager, but reading is important silly~\n\n"
                "Try again~",
            )
            continue

        return username.strip()


def first_run_setup() -> ClientConfig:
    username = prompt_username()
    device_id = str(uuid.uuid4())

    server_base_url = "wss://lmi.letmommyin.com/ws"

    cfg = ClientConfig(
        device_id=device_id,
        username=username,
        server_base_url=server_base_url,
        device_token=None,
        popup_screens=None,
        audio_device_id=None,
        # NEW defaults:
        pet_names=["pet", "toy", "darling", "sweetheart"],
        popup_sfx_path="popup.wav",

# If you want default audio off by default, set None.
# If you want it on, put a URL or file:// URL here.
        default_audio_url="https://pub-6dd573008dee4009bea8855056470713.r2.dev/OMD/better%20binaural.mp3",

# Same for overlay.
        default_overlay_url="https://pub-6dd573008dee4009bea8855056470713.r2.dev/OMD/mommy1.gif",
        default_overlay_opacity=0.3,
        default_overlay_screen=-1,
        tier = "free"
    )

    save_config(cfg)
    return cfg

def build_ws_url(cfg: ClientConfig, *, enroll_code: str | None = None) -> str:
    if enroll_code is not None:
        return f"{cfg.server_base_url}?enroll_code={enroll_code}"

    if cfg.device_token:
        return f"{cfg.server_base_url}?device_token={cfg.device_token}"

    return cfg.server_base_url


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # Avoid duplicate handlers if you restart parts of the program
    if logger.handlers:
        return

    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    fmt.converter = time.gmtime

    # File handler (rotating so it doesn't grow forever)
    file_handler = RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.INFO)

    # Console handler (still helpful during dev)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    console_handler.setLevel(logging.INFO)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
def _network_thread_main(cfg: ClientConfig, bridge: CommandBridge, ack_queue: "queue.Queue[dict]", initial_enroll_code) -> None:
    asyncio.run(run_client(cfg, bridge, ack_queue, initial_enroll_code))

async def run_client(cfg, bridge: CommandBridge, ack_queue: "queue.Queue[dict]", initial_enroll_code: str | None):
    global _NET_LOOP, _shutdown_event
    _NET_LOOP = asyncio.get_running_loop()
    _shutdown_event = asyncio.Event()  # NEW
    
    delay = 2  # seconds
    max_delay = 30

    pending_enroll_code: str | None = initial_enroll_code
    
    while True:
        if _NET_STOP.is_set():
            return
        try:
            if not cfg.device_token and pending_enroll_code is None:
                pending_enroll_code = get_enroll_code_or_exit()

            ws_url = build_ws_url(cfg, enroll_code=pending_enroll_code)
            logging.info("Connecting to %s ...", ws_url)
            async with websockets.connect(ws_url) as ws:
                global _NET_WS
                _NET_WS = ws

                bridge.command_received.emit({"type": "__tray_connected__", "connected": True})

                # Reset backoff on successful connect
                delay = 2

                hello = build_hello(cfg)
                await ws.send(json.dumps(hello))
                logging.info("Sent hello: %s", hello)
                hb_task = asyncio.create_task(heartbeat_loop(ws, interval_s=25))
                ack_task = asyncio.create_task(ack_sender_loop(ws, ack_queue))

                # Receive loop (dispatch + ack)
                try:
                    recv_task = asyncio.create_task(ws.recv())
                    shutdown_task = asyncio.create_task(_shutdown_event.wait())
                    
                    while True:
                        done, _ = await asyncio.wait(
                            [recv_task, shutdown_task],
                            return_when=asyncio.FIRST_COMPLETED
                        )
                        if shutdown_task in done:
                            break
                        raw = recv_task.result()
                        recv_task = asyncio.create_task(ws.recv())
                        data = json.loads(raw)
                        
                        cmd_type = data.get("type")
                        
                        if cmd_type == "server_status":
                            # send to tray via Qt signal-safe path:
                            ts = data.get("last_command_ts")
                            try:
                                ts_val = float(ts) if ts is not None else None
                            except Exception:
                                ts_val = None
                            # we need a Qt-thread call; simplest: emit via existing bridge
                            bridge.command_received.emit({"type": "__tray_status__", "last_command_ts": ts_val})
                            catalogue = data.get("catalogue")
                            if catalogue:
                                want_sessions, want_blocks, want_behaviors = compute_wanted(catalogue)
                                if want_sessions or want_blocks or want_behaviors:
                                    await ws.send(json.dumps({
                                        "type": "catalogue_sync",
                                        "want_sessions": want_sessions,
                                        "want_blocks": want_blocks,
                                        "want_behaviors": [
                                            {"name": n, "behavior_type": bt} 
                                            for n, bt in want_behaviors
                                        ],
                                    }))
                            continue

                        if cmd_type == "enroll_ok":
                            token = data.get("device_token")
                            if not token:
                                logging.error("Enroll_ok missing device_token")
                                raise SystemExit(1)

                            cfg.device_token = token
                            save_config(cfg)
                            logging.info("Enrollment successful; device_token saved.")

                            # Clear pending enroll code so future connections use device_token
                            pending_enroll_code = None
                            bridge.command_received.emit({"type": "__enroll_complete__"})
                            
                            # Break out to reconnect using the new token
                            break
                        
                        # client.py — inside run_client() receive loop, after server_status handling

                        if cmd_type == "tier":
                            # Bounce to Qt thread; keep config/state changes on UI thread
                            tier = (data.get("tier") or "free").strip().lower()
                            bridge.command_received.emit({"type": "__tier__", "tier": tier})
                            continue

                        cmd_id = data.get("id", "-")
                        logging.info("Received command type=%s id=%s", cmd_type, cmd_id)

                        try:
                            bridge.command_received.emit(data)
                            await send_ack(ws, cmd_id, status=ACK_RECEIVED)
                        except Exception as e:
                            logging.exception("Command failed for id=%s type=%s", cmd_id, cmd_type)
                            await send_ack(ws, cmd_id, status=ACK_FAILED, detail=str(e))
                finally:
                    _NET_WS = None
                    for t in [recv_task, shutdown_task, hb_task, ack_task]:
                        if t is not None:
                            t.cancel()
                            try:
                                await t
                            except (asyncio.CancelledError, Exception):
                                pass


        except KeyboardInterrupt:
            logging.info("KeyboardInterrupt")
            bridge.command_received.emit({"type": "__tray_connected__", "connected": False})
            return
        except Exception as e:
            # If we're trying to enroll and the connection failed, ask for a new code next time
            if cfg.device_token is None:
                pending_enroll_code = None
                
            if _NET_STOP.is_set():
                return

            # tell Qt thread we are disconnected (tray icon)
            try:
                bridge.command_received.emit({"type": "__tray_connected__", "connected": False})
            except Exception:
                pass

            logging.warning("Connection error: %r", e)
            logging.info("Reconnecting in %s seconds...", delay)
            await asyncio.sleep(delay)
            delay = min(max_delay, delay * 2)
            
def request_network_close() -> None:
    _NET_STOP.set()  # signals the while loop to not reconnect
    loop = _NET_LOOP
    ev = _shutdown_event
    if loop is not None and ev is not None:
        loop.call_soon_threadsafe(ev.set)

def start_global_pause_hotkey(on_toggle) -> None:
    """
    System-wide hotkey via pynput: Ctrl + Alt + F12
    on_toggle is called from the pynput thread (NOT Qt),
    so it should bounce work back to the Qt thread.
    """
    
    if sys.platform.startswith("linux"):
        logging.info("Global hotkey disabled on Linux to prevent Qt conflicts")
        return
    
    global _HOTKEY_LISTENER

    pressed = set()
    fired = {"down": False}  # latch so chord fires once until released

    def ctrl_down() -> bool:
        return keyboard.Key.ctrl_l in pressed or keyboard.Key.ctrl_r in pressed

    def alt_down() -> bool:
        return (
            keyboard.Key.alt_l in pressed
            or keyboard.Key.alt_r in pressed
            or keyboard.Key.alt_gr in pressed
        )

    def chord_down() -> bool:
        return ctrl_down() and alt_down() and (keyboard.Key.f12 in pressed)

    def on_press(key):
        pressed.add(key)
        if chord_down():
            if not fired["down"]:
                fired["down"] = True
                on_toggle()

    def on_release(key):
        pressed.discard(key)
        # reset latch when chord is no longer held
        if not chord_down():
            fired["down"] = False

    _HOTKEY_LISTENER = keyboard.Listener(on_press=on_press, on_release=on_release)
    _HOTKEY_LISTENER.daemon = True
    _HOTKEY_LISTENER.start()


def stop_global_pause_hotkey() -> None:
    global _HOTKEY_LISTENER
    try:
        if _HOTKEY_LISTENER is not None:
            _HOTKEY_LISTENER.stop()
    except Exception:
        pass
    finally:
        _HOTKEY_LISTENER = None

def main() -> None:
    setup_logging()
    
    app = QApplication([])
    apply_app_theme(app)
    app.setQuitOnLastWindowClosed(False)
    
    cfg = load_config()
    if cfg is None:
        cfg = first_run_setup()
        
    local_content = CONFIG_DIR / "content"
    (local_content / "sessions").mkdir(parents=True, exist_ok=True)
    (local_content / "blocks").mkdir(parents=True, exist_ok=True)

    local_root = CONFIG_DIR / "content"  # this is your writable root :contentReference[oaicite:11]{index=11}

    def _handle_injection(cmd: dict) -> None:
        t = cmd.get("type")

        if t == "inject_block":
            stem = write_injected_block(
                local_root,
                title=str(cmd.get("title") or "Untitled Block"),
                summary=str(cmd.get("summary") or ""),
                tags=list(cmd.get("tags") or []),
                intensity=cmd.get("intensity", None),
                body=str(cmd.get("body") or ""),
            )
            update_manifest_entry("blocks", str(cmd.get("title")), int(time.time()))
            _injection_notifier.add(InjectEvent(kind="block", title=stem))
            return

        if t == "inject_session":
            stem = write_injected_session(
                local_root,
                title=str(cmd.get("title") or "Untitled Session"),
                summary=str(cmd.get("summary") or ""),
                tags=list(cmd.get("tags") or []),
                intensity=cmd.get("intensity", None),
                session_json=dict(cmd.get("session_json") or {}),
            )
            update_manifest_entry("sessions", str(cmd.get("title")), int(time.time()))
            _injection_notifier.add(InjectEvent(kind="session", title=stem))
            return
        
        if t == "inject_behavior":
            behavior_type = str(cmd.get("behavior_type") or "")
            name = str(cmd.get("name") or "")
            entry = cmd.get("entry") or {}
            updated_at = cmd.get("updated_at") or int(time.time())
            
            if behavior_type not in ("toys_and_teases", "rules_and_tasks", "web_aided_tasks"):
                logging.warning("Unknown behavior_type in inject_behavior: %s", behavior_type)
                return

            write_injected_behavior(local_root, behavior_type=behavior_type, name=name, entry=entry)
            update_behavior_manifest_entry(behavior_type, name, updated_at)
            _injection_notifier.add(InjectEvent(kind="behavior", title=f"{behavior_type}: {name}"))
            return

    set_injection_handler(_handle_injection)

    cfg.pet_names = cfg.pet_names or ["pet", "toy", "darling", "sweetheart"]
    cfg.default_overlay_url = cfg.default_overlay_url or "https://pub-6dd573008dee4009bea8855056470713.r2.dev/OMD/mommy1.gif"
    cfg.default_audio_url = cfg.default_audio_url or "https://pub-6dd573008dee4009bea8855056470713.r2.dev/OMD/better%20binaural.mp3"
    if cfg.default_audio_url == "https://pub-6dd573008dee4009bea8855056470713.r2.dev/OMD/myNoise_BinauralBeats_63000063000000000000_0_5%20(1).mp3":
        cfg.default_audio_url = "https://pub-6dd573008dee4009bea8855056470713.r2.dev/OMD/better%20binaural.mp3"
    cfg.default_overlay_opacity = getattr(cfg, "default_overlay_opacity", 0.3)
    cfg.default_overlay_screen = getattr(cfg, "default_overlay_screen", -1)
    cfg.popup_sfx_path = cfg.popup_sfx_path or resource_path("popup.wav")
    default_pics = Path.home() / "Pictures" / "LMI"
    cfg.image_save_enabled = bool(getattr(cfg, "image_save_enabled", True))
    cfg.image_save_dir = getattr(cfg, "image_save_dir", None) or str(default_pics)

    save_config(cfg)

    set_image_save_enabled(cfg.image_save_enabled)
    set_image_save_dir(cfg.image_save_dir)
    
    set_popup_screens(cfg.popup_screens)
    set_pet_names(cfg.pet_names)
    set_default_audio_url(cfg.default_audio_url)
    set_default_overlay(
        cfg.default_overlay_url,
        opacity=cfg.default_overlay_opacity,
        screen=cfg.default_overlay_screen,
    )
    set_popup_sfx_path(cfg.popup_sfx_path)
    set_session_receive_mode(getattr(cfg, "session_receive_mode", "full"))
    
    args = parse_args()
    if args.server:
        parsed = urlparse(args.server.strip())
        if parsed.query:
            raise ValueError("--server must not include query parameters")
        cfg.server_base_url = args.server.strip()
    
    device_name = socket.gethostname()
    
    logging.info("Client identity loaded")
    logging.info("  username:   %s", cfg.username)
    logging.info("  device_id:  %s", cfg.device_id)
    logging.info("  device:     %s", device_name)
    logging.info("  server_base_url: %s", cfg.server_base_url)
    logging.info("  config:     %s", CONFIG_PATH)
    logging.info("  log:        %s", LOG_PATH)


    bridge = CommandBridge()
    ack_queue = queue.Queue(maxsize=1000)
    set_ack_queue(ack_queue)
    dispatcher = CommandDispatcher(ack_queue)

    session_runner = SessionRunner(dispatcher.handle_command)
    set_session_runner(session_runner)
    audio_manager = AudioManager()
    set_audio_manager(audio_manager)
    audio_manager.set_output_device_by_id(cfg.audio_device_id)
    subliminal_manager = SubliminalManager()
    set_subliminal_manager(subliminal_manager)
    wfm_manager = WfmManager()
    set_wfm_manager(wfm_manager)
    behavior_manager = BehaviorManager(
        config_dir=CONFIG_DIR,
        session_runner=session_runner,
        dispatch_command=dispatcher.handle_command,
        get_session_path=lambda stem: next(
            (p for name, p in get_session_choices() if name == stem), None
        ),
    )
    if getattr(cfg, "tier", "free") == "paid":
        behavior_manager.start()
    # --- Local sessions compiler (sessions/*.json + blocks/*.txt) ---
    # Dev: if there is a sessions/ folder next to this file, use it.
    content_roots = get_content_roots(CONFIG_DIR)
    compiler = SessionCompiler(roots=content_roots)

    # Make sure the directories exist so users can drop files in immediately.
    (compiler.sessions_dir).mkdir(parents=True, exist_ok=True)
    (compiler.blocks_dir).mkdir(parents=True, exist_ok=True)
    
    bridge.toggle_pause.connect(session_runner.toggle_pause)

    def shutdown_now() -> None:
        try:
            stop_global_pause_hotkey()
            _NET_STOP.set()
            request_network_close()

            # stop timers/managers
            session_runner.cancel()
            wfm_manager.cancel()
            subliminal_manager.stop()
            audio_manager.stop()
            stop_gif_overlays()
            behavior_manager._general_timer.stop()
            behavior_manager._autodrainer_timer.stop()

            # close dialogs
            close_all_messages()
            close_all_images()
            close_all_wfm()

            # hide tray + quit Qt
            try:
                tray.tray.hide()
            except Exception:
                pass
            app.quit()

        finally:
            # guarantee process death even if something above fails
            threading.Timer(0.2, lambda: os._exit(0)).start()

    # --- Tray icon setup (Qt thread) ---
    def get_selected_screens():
        return cfg.popup_screens

    def set_selected_screens(v):
        set_popup_screens(v)
        cfg.popup_screens = v
        save_config(cfg)

    def get_selected_audio():
        return cfg.audio_device_id

    def set_selected_audio(v):
        cfg.audio_device_id = v
        save_config(cfg)

    def get_screen_choices():
        # None means "all screens"
        screens = QApplication.screens()
        choices = [("All screens", None)]
        for i, s in enumerate(screens):
            # include geometry to help the user
            g = s.geometry()
            choices.append((f"Screen {i} ({g.width()}x{g.height()})", [i]))
        return choices

    def get_audio_choices():
        # implemented in section 4 (AudioManager helper)
        return audio_manager.get_audio_device_choices()
    
    def get_session_choices():
        seen = set()
        choices = []

        for root in content_roots:  # from get_content_roots()
            sess_dir = root / "sessions"
            for p in sorted(sess_dir.glob("*.json")):
                key = p.stem
                if key in seen:
                    continue
                seen.add(key)
                choices.append((key, p))
        return choices
    
    def toggle_pause_from_hotkey():
    # Safe cross-thread hop into Qt
        bridge.toggle_pause.emit()

    start_global_pause_hotkey(toggle_pause_from_hotkey)

    # For now you can point all states at the same icon;
    # later you can swap in MommyIcon_fresh.ico etc.
    icon_fresh = QIcon(resource_path("MommyIcon.ico"))  # reuse your existing resource_path (see below)
    icon_stale = QIcon(resource_path("MommyStale.ico"))
    icon_offline = QIcon(resource_path("MommyOff.ico"))

    tray = TrayManager(
        icon_fresh=icon_fresh,
        icon_stale=icon_stale,
        icon_offline=icon_offline,
        get_session_choices=get_session_choices,
        get_screen_choices=get_screen_choices,
        get_audio_choices=get_audio_choices,
        get_selected_screens=get_selected_screens,
        set_selected_screens=set_selected_screens,
        get_selected_audio=get_selected_audio,
        set_selected_audio=set_selected_audio,
        get_pet_names=get_pet_names,
        get_default_audio_url=get_default_audio_url,
        get_default_overlay=get_default_overlay,
    )
    
    dispatcher.cfg = cfg
    dispatcher.tray = tray
    dispatcher.behavior_manager = behavior_manager

    tray.audio_device_changed.connect(lambda dev_id: audio_manager.set_output_device_by_id(dev_id))
    tray.set_image_save_enabled_checked(cfg.image_save_enabled)
    
    def run_local_session(session_path: Path) -> None:
        try:
            compiled = compiler.compile_steps(session_path)
            # Optional: log what got chosen (useful for debugging randomness)
            logging.info("Running local session=%s chosen_blocks=%s", compiled.name, compiled.chosen_blocks)

            steps = _apply_client_session_defaults(compiled.steps)
            session_runner.start(
                session_id=f"local_{compiled.name}",
                steps=steps,
            )
        except Exception as e:
            logging.exception("Failed to run local session: %s", session_path)
            QMessageBox.critical(
                None,
                "Session failed",
                f"Could not run session:\n{session_path}\n\n{e}",
            )

    tray.session_selected.connect(run_local_session)

    tray.request_exit.connect(shutdown_now)
    
    def on_pet_names_changed(names: list[str] | None) -> None:
        cfg.pet_names = names
        save_config(cfg)
        set_pet_names(names)

    def on_default_audio_changed(url: str | None) -> None:
        cfg.default_audio_url = url
        save_config(cfg)
        set_default_audio_url(url)

    def on_default_overlay_changed(payload) -> None:
        url, opacity, screen = payload
        cfg.default_overlay_url = url
        cfg.default_overlay_opacity = float(opacity)
        cfg.default_overlay_screen = int(screen)
        save_config(cfg)
        set_default_overlay(url, opacity=cfg.default_overlay_opacity, screen=cfg.default_overlay_screen)
        
    def on_popup_sfx_changed(path: str | None) -> None:
        cfg.popup_sfx_path = path
        save_config(cfg)
        set_popup_sfx_path(path)
        
    def on_image_save_enabled_changed(enabled: bool) -> None:
        cfg.image_save_enabled = bool(enabled)
        save_config(cfg)
        set_image_save_enabled(cfg.image_save_enabled)
        tray.set_image_save_enabled_checked(cfg.image_save_enabled)

    def on_image_save_dir_changed(folder: str | None) -> None:
        folder = (folder or "").strip() or None
        cfg.image_save_dir = folder
        save_config(cfg)
        set_image_save_dir(cfg.image_save_dir)
        
    def open_session_launcher():
        def _on_allowed_changed(new_allowed: list[str]) -> None:
            behaviors = load_behaviors(CONFIG_DIR)
            behaviors["session"]["allowed_sessions"] = new_allowed
            save_behaviors(CONFIG_DIR, behaviors)
            behavior_manager.update_behaviors(behaviors)
        
        dlg = SessionLauncherDialog(
            content_roots=content_roots,
            compiler=compiler,
            allowed_sessions=load_behaviors(CONFIG_DIR)["session"]["allowed_sessions"],
            on_allowed_changed=_on_allowed_changed,
            parent=None,
            )
        if dlg.exec() == QDialog.Accepted and dlg.result:
            run_local_session(dlg.result.session_path)
    
    def open_customizer():
        # Paywall gate: only paid can open
        if getattr(cfg, "tier", "free") != "paid":
            QMessageBox.information(None, "Locked", "Session customizer is a paid feature.")
            return

        dlg = SessionCustomizerDialog(
            content_roots=content_roots,
            sessions_dir=compiler.sessions_dir,
            parent=None,
            )
        if dlg.exec() == QDialog.Accepted and dlg.result:
            logging.info("Saved custom session: %s", dlg.result.session_path)
            tray.refresh_sessions_menu()
            
    def open_behavior_settings():
        if getattr(cfg, "tier", "free") != "paid":
            QMessageBox.information(None, "Locked", "Automated behaviors are a paid feature.")
            return
        dlg = BehaviorSettingsDialog(
            config_dir=CONFIG_DIR,
            content_roots=content_roots,
            compiler=compiler,
            parent=None,
        )
        dlg.behaviors_changed.connect(behavior_manager.update_behaviors)
        dlg.exec()
            
    def on_session_receive_mode_changed(mode: str) -> None:
        mode = (mode or "").strip().lower()
        if mode not in ("full", "minimal", "off"):
            mode = "full"
        cfg.session_receive_mode = mode
        save_config(cfg)
        set_session_receive_mode(mode)

    tray.session_receive_mode_changed.connect(on_session_receive_mode_changed)

    tray.customizer_requested.connect(open_customizer)
    tray.browse_sessions_requested.connect(open_session_launcher)

    tray.image_save_enabled_changed.connect(on_image_save_enabled_changed)
    tray.image_save_dir_changed.connect(on_image_save_dir_changed)
    tray.popup_sfx_changed.connect(on_popup_sfx_changed)
    tray.pet_names_changed.connect(on_pet_names_changed)
    tray.default_audio_url_changed.connect(on_default_audio_changed)
    tray.default_overlay_changed.connect(on_default_overlay_changed)
    tray.toggle_session_pause.connect(session_runner.toggle_pause)
    
    tray.behavior_settings_requested.connect(open_behavior_settings)

    tray.fire_next_drain.connect(behavior_manager.trigger_drain) 

    tray.fire_next_event.connect(behavior_manager.trigger_next_event)

    bridge.command_received.connect(dispatcher.handle_command)

    initial_enroll_code: str | None = None
    if cfg.device_token is None:
        initial_enroll_code = get_enroll_code_or_exit()

    t = threading.Thread(target=_network_thread_main, args=(cfg, bridge, ack_queue, initial_enroll_code), daemon=True)
    t.start()

    app.exec()

if __name__ == "__main__":
    main()
