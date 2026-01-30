import json
import os
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

import websockets
import threading
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

from parser import parse_command, set_session_runner, set_audio_manager, set_subliminal_manager, set_wfm_manager, set_ack_queue
from session_runner import SessionRunner
from audio_manager import AudioManager
from subliminal_manager import SubliminalManager
from wfm_manager import WfmManager


CONFIG_DIR = Path(os.getenv("APPDATA", ".")) / "LMI"
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

def load_config() -> ClientConfig | None:
    if not CONFIG_PATH.exists():
        return None

    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)

    # New format
    if "server_base_url" in data:
        return ClientConfig(**data)

    # Unknown config format
    return None

class CommandBridge(QObject):
    command_received = Signal(dict)
    
class CommandDispatcher(QObject):
    def __init__(self, ack_queue: "queue.Queue[dict]"):
        super().__init__()
        self.ack_queue = ack_queue

    def handle_command(self, data: dict) -> None:
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
        "version": "v0.1",
        "protocol": "v0.1"
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
        item = await asyncio.to_thread(ack_queue.get)
        command_id = item.get("id", "-")
        status = item.get("status", "ack")
        detail = item.get("detail", "")
        await send_ack(ws, command_id, status=status, detail=detail)

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


def prompt_username() -> str:
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
    delay = 2  # seconds
    max_delay = 30

    pending_enroll_code: str | None = initial_enroll_code
    
    while True:
        try:
            if not cfg.device_token and pending_enroll_code is None:
                pending_enroll_code = get_enroll_code_or_exit()

            ws_url = build_ws_url(cfg, enroll_code=pending_enroll_code)
            logging.info("Connecting to %s ...", ws_url)
            async with websockets.connect(ws_url) as ws:

                # Reset backoff on successful connect
                delay = 2

                hello = build_hello(cfg)
                await ws.send(json.dumps(hello))
                logging.info("Sent hello: %s", hello)
                hb_task = asyncio.create_task(heartbeat_loop(ws, interval_s=25))
                ack_task = asyncio.create_task(ack_sender_loop(ws, ack_queue))

                # Receive loop (dispatch + ack)
                try:
                    while True:
                        raw = await ws.recv()
                        data = json.loads(raw)
                        
                        cmd_type = data.get("type")
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
                            
                            # Break out to reconnect using the new token
                            break

                        cmd_id = data.get("id", "-")
                        logging.info("Received command type=%s id=%s", cmd_type, cmd_id)

                        try:
                            bridge.command_received.emit(data)
                            await send_ack(ws, cmd_id, status=ACK_RECEIVED)
                        except Exception as e:
                            logging.exception("Command failed for id=%s type=%s", cmd_id, cmd_type)
                            await send_ack(ws, cmd_id, status=ACK_FAILED, detail=str(e))
                finally:
                    hb_task.cancel()
                    try:
                        await hb_task
                    except asyncio.CancelledError:
                        pass
                    ack_task.cancel()
                    try:
                        await ack_task
                    except asyncio.CancelledError:
                        pass


        except KeyboardInterrupt:
            logging.info("KeyboardInterrupt")
            return
        except Exception as e:
            # If we're trying to enroll and the connection failed, ask for a new code next time
            if cfg.device_token is None:
                pending_enroll_code = None

            logging.warning("Connection error: %r", e)
            logging.info("Reconnecting in %s seconds...", delay)
            await asyncio.sleep(delay)
            delay = min(max_delay, delay * 2)

def main() -> None:
    setup_logging()
    
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    
    cfg = load_config()
    if cfg is None:
        cfg = first_run_setup()
        
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
    subliminal_manager = SubliminalManager()
    set_subliminal_manager(subliminal_manager)
    wfm_manager = WfmManager()
    set_wfm_manager(wfm_manager)

    bridge.command_received.connect(dispatcher.handle_command)

    initial_enroll_code: str | None = None
    if cfg.device_token is None:
        initial_enroll_code = get_enroll_code_or_exit()

    t = threading.Thread(target=_network_thread_main, args=(cfg, bridge, ack_queue, initial_enroll_code), daemon=True)
    t.start()

    app.exec()

if __name__ == "__main__":
    main()
