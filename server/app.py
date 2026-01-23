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

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException, Form
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI(title="Command Hub PoC")
AGENT_TOKEN = os.getenv("AGENT_TOKEN", "")

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

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
    # Token gate (only enforced if AGENT_TOKEN is set)
    if AGENT_TOKEN:
        token = ws.query_params.get("token")
        if token != AGENT_TOKEN:
            await ws.close(code=1008)
            return
    await ws.accept()

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

            mtype = msg.get("type")

            if mtype == "heartbeat":
                hub.update_last_seen(device_id)
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
