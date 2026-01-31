# -*- coding: utf-8 -*-
"""
Created on Wed Jan 21 10:32:03 2026

@author: MommyDahlia
"""

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict
import sqlite3
import hashlib
import secrets

from fastapi import FastAPI, APIRouter, Request, WebSocket, WebSocketDisconnect, HTTPException, Form, UploadFile, File, Body
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

PROTOCOL_VERSION = "v0.1"
MAX_LOG_EVENTS = 1000

def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")

        # One-time enrollment codes (hashed). "used_at" set when consumed.
        conn.execute("""
        CREATE TABLE IF NOT EXISTS enroll_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code_hash TEXT NOT NULL UNIQUE,
            expires_at INTEGER NOT NULL,
            used_at INTEGER,
            created_at INTEGER NOT NULL
        );
        """)

        # Devices enrolled into the system.
        # token_hash is the long-lived device credential (hashed).
        conn.execute("""
        CREATE TABLE IF NOT EXISTS devices (
            device_id TEXT PRIMARY KEY,
            token_hash TEXT NOT NULL UNIQUE,
            created_at INTEGER NOT NULL,
            last_seen INTEGER,
            username TEXT,
            device_name TEXT
        );
        """)

        conn.commit()

DB_PATH = "lmi.db"
init_db()

app = FastAPI(title="Command Hub PoC")
admin_router = APIRouter(prefix="/admin")

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def create_enroll_code(ttl_seconds: int = 15 * 60) -> tuple[str, int]:
    """
    Returns (raw_code, expires_at_epoch).
    Store only the hash in DB.
    """
    now = int(time.time())
    expires_at = now + ttl_seconds

    # URL-safe and easy to copy/paste; strip padding for aesthetics
    raw_code = secrets.token_urlsafe(16).rstrip("=")
    code_hash = sha256_hex(raw_code)

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO enroll_codes (code_hash, expires_at, used_at, created_at) VALUES (?, ?, NULL, ?)",
            (code_hash, expires_at, now),
        )
        conn.commit()

    return raw_code, expires_at

@admin_router.post("/enroll/create")
def admin_create_enroll_code(ttl_minutes: int = 15):
    ttl_seconds = ttl_minutes * 60
    code, expires_at = create_enroll_code(ttl_seconds=ttl_seconds)
    return {"code": code, "expires_at": expires_at}

app.include_router(admin_router)


def consume_enroll_code(raw_code: str) -> bool:
    """
    Marks the code as used if it exists, is unexpired, and unused.
    Returns True if successfully consumed, otherwise False.
    """
    now = int(time.time())
    code_hash = sha256_hex(raw_code)

    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            """
            UPDATE enroll_codes
            SET used_at = ?
            WHERE code_hash = ?
              AND used_at IS NULL
              AND expires_at >= ?
            """,
            (now, code_hash, now),
        )
        conn.commit()

        return cur.rowcount == 1

def generate_device_token() -> str:
    # long-lived secret stored on the client
    return secrets.token_urlsafe(32).rstrip("=")


def get_device_id_for_token(device_token: str) -> str | None:
    token_hash = sha256_hex(device_token)
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT device_id FROM devices WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()
        return row[0] if row else None


def device_exists(device_id: str) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT 1 FROM devices WHERE device_id = ?",
            (device_id,),
        ).fetchone()
        return row is not None


def create_device(device_id: str, device_token: str, username: str | None, device_name: str | None) -> None:
    now = int(time.time())
    token_hash = sha256_hex(device_token)

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO devices (device_id, token_hash, created_at, last_seen, username, device_name)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (device_id, token_hash, now, now, username, device_name),
        )
        conn.commit()


def update_device_metadata(device_id: str, username: str | None, device_name: str | None, *, allow_identity_change=False):
    now = int(time.time())
    with sqlite3.connect(DB_PATH) as conn:
        if allow_identity_change:
            conn.execute(
                """
                UPDATE devices
                SET last_seen = ?, username = COALESCE(?, username), device_name = COALESCE(?, device_name)
                WHERE device_id = ?
                """,
                (now, username, device_name, device_id),
            )
        else:
            conn.execute(
                "UPDATE devices SET last_seen = ? WHERE device_id = ?",
                (now, device_id),
            )
        conn.commit()


@app.get("/health")
def health():
    return {"ok": True, "ts": time.time()}

@dataclass
class DeviceInfo:
    device_id: str
    username: str
    device_name: str = "unknown"
    version: str = "0.0"
    connected_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)

@dataclass
class LogEvent:
    ts: float
    device_id: str
    event: str           # e.g., "connect", "disconnect", "sent", "ack"
    detail: str = ""
    command_id: str = "-"

class Hub:
    def __init__(self):
        # device_id -> WebSocket
        self.connections: Dict[str, WebSocket] = {}

        # device_id -> DeviceInfo
        self.devices: Dict[str, DeviceInfo] = {}

        # simple event log (in memory for PoC)
        self.logs: list[LogEvent] = []
        
        self.last_command_ts: float | None = None

    def register(self, device: DeviceInfo, ws: WebSocket):
        self.connections[device.device_id] = ws
        self.devices[device.device_id] = device

        self.logs.append(
            LogEvent(
                ts=time.time(),
                device_id=device.device_id,
                event="connect",
                detail=f"{device.username} @ {device.device_name}"
            )
        )
        if len(self.logs) > MAX_LOG_EVENTS:
            self.logs.pop(0)

    def unregister(self, device_id: str):
        self.connections.pop(device_id, None)

        dev = self.devices.get(device_id)
        if dev:
            self.logs.append(
                LogEvent(
                    ts=time.time(),
                    device_id=device_id,
                    event="disconnect",
                    detail=f"{dev.username} @ {dev.device_name}"
                )
            )
            if len(self.logs) > MAX_LOG_EVENTS:
                self.logs.pop(0)

    def update_last_seen(self, device_id: str):
        dev = self.devices.get(device_id)
        if dev:
            dev.last_seen = time.time()
    
    def log(self, device_id: str, event: str, detail: str = "", command_id: str = "-"):
        if event == "sent":
            self.last_command_ts = time.time()
        self.logs.append(
            LogEvent(
                ts=time.time(),
                device_id=device_id,
                event=event,
                detail=detail,
                command_id=command_id,
            )
        )
        if len(self.logs) > MAX_LOG_EVENTS:
            self.logs.pop(0)

    def handle_ack(self, device_id: str, msg: dict):
        # Expect: {type:"ack", id:"cmd_x", status:"shown", detail:""}
        cmd_id = str(msg.get("id", "-"))
        status = str(msg.get("status", "ack"))
        detail = str(msg.get("detail", ""))
        
        self.log(device_id=device_id, event="ack", detail=f"{status} {detail}".strip(), command_id=cmd_id)

            
hub = Hub()

@app.get("/devices")
def get_devices():
    items = []
    for d in hub.devices.values():
        items.append({
            "device_id": d.device_id,
            "username": d.username,
            "device_name": d.device_name,
            "version": d.version,
            "connected_at": d.connected_at,
            "last_seen": d.last_seen,
            "online": d.device_id in hub.connections,
        })

    # Sort: online first, then most recently seen
    items.sort(key=lambda x: (not x["online"], -x["last_seen"]))
    return {"devices": items}

@app.get("/device/{device_id}", response_class=HTMLResponse)
def device_page(request: Request, device_id: str):
    d = hub.devices.get(device_id)
    if not d:
        raise HTTPException(status_code=404, detail="Unknown device")

    device = {
        "device_id": d.device_id,
        "username": d.username,
        "device_name": d.device_name,
        "version": d.version,
        "connected_at": d.connected_at,
        "last_seen": d.last_seen,
        "online": d.device_id in hub.connections,
    }

    logs = [l for l in hub.logs if l.device_id == device_id][-50:]

    return templates.TemplateResponse(
        "device.html",
        {"request": request, "device": device, "logs": logs},
    )

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    devices = []
    for d in hub.devices.values():
        devices.append({
            "device_id": d.device_id,
            "username": d.username,
            "device_name": d.device_name,
            "version": d.version,
            "connected_at": d.connected_at,
            "last_seen": d.last_seen,
            "online": d.device_id in hub.connections,
        })

    devices.sort(key=lambda x: (not x["online"], -x["last_seen"]))
    return templates.TemplateResponse("index.html", {"request": request, "devices": devices})

@app.post("/device/{device_id}/message")
async def send_message_htmx(
    device_id: str,
    title: str = Form(...),
    body: str = Form(...),
    level: str = Form("info"),
    lifespan_s: str = Form(""),
):
    # If no active websocket, we can't push a command
    if device_id not in hub.connections:
        return PlainTextResponse("Device is offline (no active connection).", status_code=409)

    cmd_id = f"cmd_{uuid.uuid4().hex[:10]}"
    
    lifespan_s = lifespan_s.strip()
    lifespan_val: int | None = None
    if lifespan_s != "":
        lifespan_val = int(lifespan_s)
    else:
        lifespan_val = 8

    payload = {
        "type": "show_message",
        "id": cmd_id,
        "title": title,
        "body": body,
        "level": level,
        "lifespan_s": lifespan_val,
    }

    ws = hub.connections[device_id]
    await ws.send_text(json.dumps(payload))

    hub.log(device_id, "sent", detail=f"show_message level={level}", command_id=cmd_id)

    return PlainTextResponse(f"Sent {cmd_id}")

@app.post("/device/{device_id}/open_url")
async def open_url_htmx(
    device_id: str,
    url: str = Form(...),
):
    # If no active websocket, we can't push a command
    if device_id not in hub.connections:
        return PlainTextResponse("Device is offline (no active connection).", status_code=409)

    url = url.strip()
    if not url:
        return PlainTextResponse("URL is required.", status_code=400)

    cmd_id = f"cmd_{uuid.uuid4().hex[:10]}"
    payload = {
        "type": "open_url",
        "id": cmd_id,
        "body": url,
    }

    ws = hub.connections[device_id]
    await ws.send_text(json.dumps(payload))

    hub.log(device_id, "sent", detail="open_url", command_id=cmd_id)

    return PlainTextResponse(f"Sent {cmd_id}")

@app.post("/device/{device_id}/image_popup")
async def image_popup_htmx(
    device_id: str,
    url: str = Form(...),
    title: str = Form(""),
):
    if device_id not in hub.connections:
        return PlainTextResponse("Device is offline (no active connection).", status_code=409)

    url = url.strip()
    if not url:
        return PlainTextResponse("URL is required.", status_code=400)

    cmd_id = f"cmd_{uuid.uuid4().hex[:10]}"
    payload = {
        "type": "image_popup",
        "id": cmd_id,
        "body": url,
    }

    ws = hub.connections[device_id]
    await ws.send_text(json.dumps(payload))

    hub.log(device_id, "sent", detail="image_popup", command_id=cmd_id)

    return PlainTextResponse(f"Sent {cmd_id}")

@app.post("/device/{device_id}/session/start")
async def session_start_htmx(
    device_id: str,
    session_file: UploadFile = File(...),
):
    if device_id not in hub.connections:
        return PlainTextResponse("Device is offline (no active connection).", status_code=409)

    # Read uploaded file bytes
    raw = await session_file.read()
    if not raw:
        return PlainTextResponse("Session file was empty.", status_code=400)

    # Decode (assume UTF-8 JSON)
    try:
        text = raw.decode("utf-8")
    except Exception:
        return PlainTextResponse("Session file must be UTF-8 text.", status_code=400)

    try:
        steps = json.loads(text)
    except Exception as e:
        return PlainTextResponse(f"Invalid JSON: {e}", status_code=400)

    if not isinstance(steps, list):
        return PlainTextResponse("Session JSON must be a JSON list of steps.", status_code=400)

    # Light validation
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            return PlainTextResponse(f"Step {i} must be an object.", status_code=400)
        if "type" not in step:
            return PlainTextResponse(f"Step {i} missing 'type'.", status_code=400)

    cmd_id = f"cmd_{uuid.uuid4().hex[:10]}"
    session_id = f"sess_{uuid.uuid4().hex[:10]}"

    payload = {
        "type": "session_start",
        "id": cmd_id,
        "session_id": session_id,
        "body": steps,
    }

    ws = hub.connections[device_id]
    await ws.send_text(json.dumps(payload))

    hub.log(device_id, "sent", detail=f"session_start file={session_file.filename} steps={len(steps)}", command_id=cmd_id)

    return PlainTextResponse(f"Sent {cmd_id} session_id={session_id}")

@app.post("/device/{device_id}/write_for_me")
async def write_for_me_htmx(
    device_id: str,
    text: str = Form(...),
    reps: str = Form("5"),
):
    if device_id not in hub.connections:
        return PlainTextResponse("Device is offline (no active connection).", status_code=409)

    text = (text or "").strip()
    if not text:
        return PlainTextResponse("Text is required.", status_code=400)

    try:
        reps_val = int(reps)
    except Exception:
        return PlainTextResponse("reps must be an integer.", status_code=400)

    if reps_val < 1 or reps_val > 500:
        return PlainTextResponse("reps out of range (1-500).", status_code=400)

    cmd_id = f"cmd_{uuid.uuid4().hex[:10]}"
    payload = {
        "type": "write_for_me",
        "id": cmd_id,
        "text": text,
        "reps": reps_val,
    }

    ws = hub.connections[device_id]
    await ws.send_text(json.dumps(payload))

    hub.log(device_id, "sent", detail=f"write_for_me reps={reps_val}", command_id=cmd_id)

    return PlainTextResponse(f"Sent {cmd_id}")

@app.post("/device/{device_id}/gif_overlay")
async def gif_overlay_htmx(
    device_id: str,
    url: str = Form(...),
    opacity: str = Form("1.0"),
    screen: str = Form("-1"),   # -1 = all screens
):
    if device_id not in hub.connections:
        return PlainTextResponse("Device is offline (no active connection).", status_code=409)

    url = url.strip()
    if not url:
        return PlainTextResponse("URL is required.", status_code=400)

    try:
        opacity_val = float(opacity)
        screen_val = int(screen)
    except Exception:
        return PlainTextResponse("Bad opacity/screen.", status_code=400)

    # Clamp opacity to sane range
    if opacity_val < 0.0:
        opacity_val = 0.0
    if opacity_val > 1.0:
        opacity_val = 1.0

    cmd_id = f"cmd_{uuid.uuid4().hex[:10]}"
    payload = {
        "type": "gif_overlay",
        "id": cmd_id,
        "url": url,
        "opacity": opacity_val,
        "screen": screen_val,
    }

    ws = hub.connections[device_id]
    await ws.send_text(json.dumps(payload))

    hub.log(device_id, "sent", detail="gif_overlay", command_id=cmd_id)
    return PlainTextResponse(f"Sent {cmd_id}")

@app.post("/device/{device_id}/gif_overlay/stop")
async def gif_overlay_stop_htmx(device_id: str):
    if device_id not in hub.connections:
        return PlainTextResponse("Device is offline (no active connection).", status_code=409)

    cmd_id = f"cmd_{uuid.uuid4().hex[:10]}"
    payload = {"type": "gif_overlay_stop", "id": cmd_id}

    ws = hub.connections[device_id]
    await ws.send_text(json.dumps(payload))

    hub.log(device_id, "sent", detail="gif_overlay_stop", command_id=cmd_id)
    return PlainTextResponse(f"Sent {cmd_id}")

@app.post("/device/{device_id}/audio_play")
async def audio_play_htmx(
    device_id: str,
    url: str = Form(...),
    volume: str = Form("0.8"),
    loop: str = Form("true"),
    duration_s: str = Form(""),   # blank = no auto-stop
):
    if device_id not in hub.connections:
        return PlainTextResponse("Device is offline (no active connection).", status_code=409)

    url = url.strip()
    if not url:
        return PlainTextResponse("URL is required.", status_code=400)

    try:
        volume_val = float(volume)
    except Exception:
        return PlainTextResponse("volume must be a number (0.0 - 1.0).", status_code=400)

    loop_val = (loop.strip().lower() in ("1", "true", "yes", "on"))

    duration_val = None
    duration_s = duration_s.strip()
    if duration_s != "":
        try:
            duration_val = float(duration_s)
        except Exception:
            return PlainTextResponse("duration_s must be a number (seconds).", status_code=400)

    cmd_id = f"cmd_{uuid.uuid4().hex[:10]}"
    payload = {
        "type": "audio_play",
        "id": cmd_id,
        "url": url,
        "volume": volume_val,
        "loop": loop_val,
        "duration_s": duration_val,
    }

    ws = hub.connections[device_id]
    await ws.send_text(json.dumps(payload))
    hub.log(device_id, "sent", detail="audio_play", command_id=cmd_id)
    return PlainTextResponse(f"Sent {cmd_id}")

@app.post("/device/{device_id}/audio_stop")
async def audio_stop_htmx(device_id: str):
    if device_id not in hub.connections:
        return PlainTextResponse("Device is offline (no active connection).", status_code=409)

    cmd_id = f"cmd_{uuid.uuid4().hex[:10]}"
    payload = {"type": "audio_stop", "id": cmd_id}

    ws = hub.connections[device_id]
    await ws.send_text(json.dumps(payload))
    hub.log(device_id, "sent", detail="audio_stop", command_id=cmd_id)
    return PlainTextResponse(f"Sent {cmd_id}")

@app.post("/device/{device_id}/subliminal_start")
async def subliminal_start_htmx(
    device_id: str,
    messages: str = Form(...),      # multiline textarea; one message per line
    duration_s: str = Form("10"),
    interval_ms: str = Form("50"),
    flash_ms: str = Form("16"),
    font_pt: str = Form("18"),
):
    if device_id not in hub.connections:
        return PlainTextResponse("Device is offline (no active connection).", status_code=409)

    # Parse messages (one per line)
    msgs = [m.strip() for m in (messages or "").splitlines() if m.strip()]
    if not msgs:
        return PlainTextResponse("messages is required (one per line).", status_code=400)

    try:
        duration_val = float(duration_s)
        interval_val = int(interval_ms)
        flash_val = int(flash_ms)
        font_val = int(font_pt)
    except Exception:
        return PlainTextResponse("Bad numeric field(s).", status_code=400)

    cmd_id = f"cmd_{uuid.uuid4().hex[:10]}"
    payload = {
        "type": "subliminal_start",
        "id": cmd_id,
        "messages": msgs,
        "duration_s": duration_val,
        "interval_ms": interval_val,
        "flash_ms": flash_val,
        "font_pt": font_val,
    }

    ws = hub.connections[device_id]
    await ws.send_text(json.dumps(payload))
    hub.log(device_id, "sent", detail="subliminal_start", command_id=cmd_id)
    return PlainTextResponse(f"Sent {cmd_id}")

@app.post("/device/{device_id}/subliminal_stop")
async def subliminal_stop_htmx(device_id: str):
    if device_id not in hub.connections:
        return PlainTextResponse("Device is offline (no active connection).", status_code=409)

    cmd_id = f"cmd_{uuid.uuid4().hex[:10]}"
    payload = {"type": "subliminal_stop", "id": cmd_id}

    ws = hub.connections[device_id]
    await ws.send_text(json.dumps(payload))
    hub.log(device_id, "sent", detail="subliminal_stop", command_id=cmd_id)
    return PlainTextResponse(f"Sent {cmd_id}")

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    
    device_token = ws.query_params.get("device_token")
    enroll_code = ws.query_params.get("enroll_code")


    if (device_token is None) == (enroll_code is None):
        # either both provided or neither provided
        await ws.close(code=1008)
        return
    
    expected_device_id = None
    if device_token:
        expected_device_id = get_device_id_for_token(device_token)
        if not expected_device_id:
            await ws.close(code=1008)
            return

    if enroll_code:
        if not consume_enroll_code(enroll_code):
            await ws.close(code=1008)
            return


    device_id = None
    try:
        # 1) Require the first message to be a "hello"
        raw = await ws.receive_text()
        msg = json.loads(raw)

        if msg.get("type") != "hello":
            # 1008 = Policy Violation (client didn't follow our protocol)
            await ws.close(code=1008)
            return

        # 2) Extract and validate identity fields
        device_id = str(msg.get("device_id", "")).strip()
        username = str(msg.get("username", "")).strip()
        device_name = str(msg.get("device_name", "")).strip()
        version = str(msg.get("version", "v0.0")).strip()
        protocol = str(msg.get("protocol","v0.0")).strip()
        
        if protocol != PROTOCOL_VERSION:
            await ws.close(code=1008)
            return


        if not device_id or not username:
            await ws.close(code=1008)
            return
        
        if expected_device_id is not None and device_id != expected_device_id:
            await ws.close(code=1008)
            return
        
        # Token-auth: expected_device_id is set, enroll_code is None
        # Enroll-auth: enroll_code is set, expected_device_id is None

        if enroll_code is not None:
        # Enrollment path: mint and bind a brand-new device token
            if device_exists(device_id):
                await ws.close(code=1008)
                return

            new_token = generate_device_token()
            create_device(
                device_id=device_id,
                device_token=new_token,
                username=username,
                device_name=device_name,
            )

            await ws.send_text(json.dumps({
                "type": "enroll_ok",
                "device_token": new_token,
            }))
            update_device_metadata(device_id, username=username, device_name=device_name,allow_identity_change=True)
        else:
            update_device_metadata(device_id, username=username, device_name=device_name)

        
        

        # 3) Register device in the hub
        dev = DeviceInfo(
            device_id=device_id,
            username=username,
            device_name=device_name or "unknown",
            version=version,
        )
        hub.register(dev, ws)

        # 4) Keep the connection alive (we'll fill this in next chunk)
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)

            # Any message means the device is alive
            hub.update_last_seen(device_id)
            update_device_metadata(device_id, None, None)

            mtype = msg.get("type")

            if mtype == "heartbeat":
                await ws.send_text(json.dumps({
                    "type": "server_status",
                    "last_command_ts": hub.last_command_ts,
                }))
                hub.update_last_seen(device_id)
                update_device_metadata(device_id, None, None)
            elif mtype == "ack":
                hub.handle_ack(device_id, msg)
            else:
                hub.log(device_id, "client_message", detail=f"unknown type: {mtype}")
                await ws.close(code=1003)
                return

    except WebSocketDisconnect:
        # Client disconnected normally
        pass
    finally:
        if device_id:
            hub.unregister(device_id)
