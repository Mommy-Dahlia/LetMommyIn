# -*- coding: utf-8 -*-
"""
Created on Wed Jan 21 10:32:03 2026

@author: MommyDahlia
"""

import json
import time
import os
import uuid
from dataclasses import dataclass, field
from typing import Dict
import sqlite3
import hashlib
import secrets

from fastapi import FastAPI, APIRouter, Request, WebSocket, WebSocketDisconnect, HTTPException, Form
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

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
app.include_router(admin_router)

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


def update_device_metadata(device_id: str, username: str | None, device_name: str | None) -> None:
    now = int(time.time())
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            UPDATE devices
            SET last_seen = ?, username = COALESCE(?, username), device_name = COALESCE(?, device_name)
            WHERE device_id = ?
            """,
            (now, username, device_name, device_id),
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

    def update_last_seen(self, device_id: str):
        dev = self.devices.get(device_id)
        if dev:
            dev.last_seen = time.time()
    
    def log(self, device_id: str, event: str, detail: str = "", command_id: str = "-"):
        self.logs.append(
            LogEvent(
                ts=time.time(),
                device_id=device_id,
                event=event,
                detail=detail,
                command_id=command_id,
            )
        )

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
):
    # If no active websocket, we can't push a command
    if device_id not in hub.connections:
        return PlainTextResponse("Device is offline (no active connection).", status_code=409)

    cmd_id = f"cmd_{uuid.uuid4().hex[:10]}"
    payload = {
        "type": "show_message",
        "id": cmd_id,
        "title": title,
        "body": body,
        "level": level,
    }

    ws = hub.connections[device_id]
    await ws.send_text(json.dumps(payload))

    hub.log(device_id, "sent", detail=f"show_message level={level}", command_id=cmd_id)

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
        version = str(msg.get("version", "0.0")).strip()

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
        else:
            # Existing device path: we already validated device_token earlier
            # Optionally you can send an info message, but do NOT send enroll_ok
            pass

        
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
                hub.update_last_seen(device_id)
                update_device_metadata(device_id, None, None)
            elif mtype == "ack":
                hub.handle_ack(device_id, msg)
            else:
                hub.log(device_id, "client_message", detail=f"unknown type: {mtype}")

    except WebSocketDisconnect:
        # Client disconnected normally
        pass
    finally:
        if device_id:
            hub.unregister(device_id)
