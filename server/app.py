# -*- coding: utf-8 -*-
"""
Created on Wed Jan 21 10:32:03 2026

@author: MommyDahlia
"""

import json
import time
import uuid
import csv
import io
import random
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict
import sqlite3
import hashlib
import secrets
import TheFactory
import logging

from fastapi import FastAPI, APIRouter, Request, WebSocket, WebSocketDisconnect, HTTPException, Form, UploadFile, File, Body
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from datetime import datetime
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")
PROTOCOL_VERSION = "v0.2"
MAX_LOG_EVENTS = 1000

def fmt_unix_et(ts: int | float | str | None) -> str:
    """
    Convert unix seconds -> US Eastern time string.
    """
    if ts is None:
        return ""
    try:
        ts_f = float(ts)
    except Exception:
        return str(ts)
    dt = datetime.fromtimestamp(ts_f, tz=_ET)
    return dt.strftime("%Y-%m-%d %I:%M:%S %p %Z")

def as_ts(v) -> float:
    try:
        return float(v)
    except Exception:
        return 0.0

def _try_alter(conn: sqlite3.Connection, sql: str) -> None:
    """
    Best-effort SQLite migration helper.
    SQLite raises OperationalError if the column already exists.
    """
    try:
        conn.execute(sql)
    except sqlite3.OperationalError:
        pass

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
        
        # app.py — inside init_db()
        
        conn.execute("""
        CREATE TABLE IF NOT EXISTS blocks (
            title TEXT PRIMARY KEY,
            summary TEXT,
            tags_json TEXT NOT NULL DEFAULT '[]',
            intensity INTEGER,
            body TEXT NOT NULL
        );
        """)
        
        # --- Sessions (server-saved mixes) ---
        conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            title TEXT PRIMARY KEY,
            summary TEXT NOT NULL DEFAULT '',
            tags_json TEXT NOT NULL DEFAULT '[]',
            intensity INTEGER,
            plan_json TEXT NOT NULL
        );
        """)
        
        conn.execute("""
        CREATE TABLE IF NOT EXISTS pending_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            delivered_at INTEGER
        );
        """)
        
        # Broadcast history (for "send to all" / "send to paid" catch-up)
        conn.execute("""
                     CREATE TABLE IF NOT EXISTS broadcast_catalogue_sessions (
                         title TEXT PRIMARY KEY,
                         audience TEXT NOT NULL,
                         payload_json TEXT NOT NULL,
                         updated_at INTEGER NOT NULL
                     );
                     """)

        conn.execute("""
                     CREATE TABLE IF NOT EXISTS broadcast_catalogue_blocks (
                         title TEXT PRIMARY KEY,
                         audience TEXT NOT NULL,
                         payload_json TEXT NOT NULL,
                         updated_at INTEGER NOT NULL
                     );
                     """)
        
        _try_alter(conn, "ALTER TABLE devices ADD COLUMN tier TEXT NOT NULL DEFAULT 'free';")

        conn.commit()

DB_PATH = "lmi.db"
init_db()

app = FastAPI(title="Command Hub PoC")
admin_router = APIRouter(prefix="/admin")

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def normalize_newlines(text: str) -> str:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")

def normalize_tags_csv(tags_str: str) -> list[str]:
    # "a, b, c" -> ["a", "b", "c"] (lowercase, dedupe)
    raw = (tags_str or "").replace(";", ",")
    items = [t.strip().lower() for t in raw.split(",") if t.strip()]
    out: list[str] = []
    seen = set()
    for t in items:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out

def _json_dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)

def _json_loads(s: str):
    try:
        return json.loads(s)
    except Exception:
        return None

def block_exists(title: str) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT 1 FROM blocks WHERE title = ?", (title,)).fetchone()
        return row is not None
    
def load_block_lines_from_db(title: str) -> list[str]:
    """
    Load a block by *title* (PK) and return its body as a list of lines.

    - Raises KeyError if the block doesn't exist
    - Normalizes newlines so compilation behaves consistently across platforms
    """
    title = (title or "").strip()
    if not title:
        raise ValueError("block title is required")

    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT body FROM blocks WHERE title = ?",
            (title,),
        ).fetchone()

    if not row:
        raise KeyError(f"Unknown block: {title}")

    body = row[0] or ""
    body = normalize_newlines(body)

    # splitlines() drops the trailing empty line if the file ends with \n.
    # That's fine for your use-case; if you ever need to preserve it, we can adjust.
    lines = body.splitlines()

    return lines

def upsert_block(*, title: str, summary: str | None, tags: list[str], intensity: int | None, body: str, overwrite: bool) -> tuple[bool, bool]:
    """
    Returns (created, overwritten).
    - If exists and overwrite=False -> raises HTTPException(409)
    """
    title = (title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")

    body = normalize_newlines(body)
    if not body.strip():
        raise HTTPException(status_code=400, detail="body must be non-empty")

    if block_exists(title) and not overwrite:
        raise HTTPException(status_code=409, detail=f"Block '{title}' already exists. Re-submit with overwrite=1 to replace it.")

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
        INSERT INTO blocks (title, summary, tags_json, intensity, body)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(title) DO UPDATE SET
            summary=excluded.summary,
            tags_json=excluded.tags_json,
            intensity=excluded.intensity,
            body=excluded.body
        """, (title, (summary or "").strip() or None, _json_dumps(tags or []), intensity, body))
        conn.commit()

    # We can’t easily distinguish created vs updated without an extra query; keep it simple:
    return (False, bool(overwrite))

def session_exists(title: str) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT 1 FROM sessions WHERE title = ?", (title,)).fetchone()
        return row is not None

def upsert_session(*, title: str, summary: str, tags: list[str], intensity: int | None, plan: dict, overwrite: bool) -> None:
    title = (title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")

    if session_exists(title) and not overwrite:
        raise HTTPException(status_code=409, detail=f"Session '{title}' already exists. Re-submit with overwrite=1 to replace it.")

    tags = [t.strip().lower() for t in (tags or []) if t and t.strip()]
    plan_json = json.dumps(plan, ensure_ascii=False)
    tags_json = json.dumps(tags, ensure_ascii=False)

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
        INSERT INTO sessions (title, summary, tags_json, intensity, plan_json)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(title) DO UPDATE SET
            summary=excluded.summary,
            tags_json=excluded.tags_json,
            intensity=excluded.intensity,
            plan_json=excluded.plan_json
        """, (title, summary or "", tags_json, intensity, plan_json))
        conn.commit()
        
def get_session_meta_by_title(title: str) -> dict | None:
    """
    Returns {"title","summary","tags","intensity","plan"} or None.

    Note: sessions table stores tags_json + plan_json (no chosen_blocks column).
    """
    title = (title or "").strip()
    if not title:
        return None

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT title, summary, tags_json, intensity, plan_json FROM sessions WHERE title = ?",
            (title,),
        ).fetchone()

    if not row:
        return None

    try:
        tags = json.loads(row["tags_json"] or "[]")
    except Exception:
        tags = []

    try:
        plan = json.loads(row["plan_json"])
    except Exception:
        plan = {"plan": []}

    return {
        "title": row["title"],
        "summary": row["summary"] or "",
        "tags": tags if isinstance(tags, list) else [],
        "intensity": row["intensity"],
        "plan": plan,
    }

def list_sessions() -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("""
            SELECT title, summary, tags_json, intensity
            FROM sessions
            ORDER BY title COLLATE NOCASE
        """).fetchall()

    out = []
    for (title, summary, tags_json, intensity) in rows:
        try:
            tags = json.loads(tags_json or "[]")
        except Exception:
            tags = []
        out.append({
            "title": title,
            "summary": summary or "",
            "tags": tags if isinstance(tags, list) else [],
            "intensity": intensity,
        })
    return out

def list_blocks() -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("""
            SELECT title, summary, tags_json, intensity
            FROM blocks
            ORDER BY title COLLATE NOCASE
        """).fetchall()

    out = []
    for (title, summary, tags_json, intensity) in rows:
        try:
            tags = json.loads(tags_json or "[]")
        except Exception:
            tags = []
        out.append({
            "title": title,
            "summary": summary or "",
            "tags": tags if isinstance(tags, list) else [],
            "intensity": intensity,
        })
    return out

def load_session_plan(title: str) -> dict:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT plan_json FROM sessions WHERE title = ?", (title,)).fetchone()
    if not row:
        raise KeyError(f"Unknown session: {title}")
    return json.loads(row[0])

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

def compile_plan_to_steps(plan: dict) -> tuple[list[dict], list[str]]:
    """
    Returns (steps, chosen_blocks)
    steps is a JSON-serializable list[dict] suitable for session_start payload["body"]
    """
    if not isinstance(plan, dict):
        raise ValueError("plan must be an object")
    items = plan.get("plan")
    if not isinstance(items, list):
        raise ValueError("plan.plan must be a list")

    seed = plan.get("seed", None)
    rng = random.Random(seed)

    chosen_blocks: list[str] = []
    out_lines: list[str] = []

    for item in items:
        if not isinstance(item, dict):
            raise ValueError("each plan item must be an object")

        if "include" in item:
            name = str(item["include"])
            chosen_blocks.append(name)
            out_lines.extend(load_block_lines_from_db(name))  # <-- you provide this
            continue

        if "lines" in item:
            lines = item["lines"]
            if not isinstance(lines, list) or not all(isinstance(x, str) for x in lines):
                raise ValueError("lines must be a list[str]")
            out_lines.extend(lines)
            continue

        if "choose" in item:
            spec = item["choose"]
            if not isinstance(spec, dict):
                raise ValueError("choose must be an object")
            names = spec.get("from")
            if not isinstance(names, list) or not names:
                raise ValueError("choose.from must be a non-empty list")

            min_n = int(spec.get("min", 1))
            max_n = int(spec.get("max", min_n))
            names = [str(x) for x in names]

            if max_n > len(names):
                max_n = len(names)
            if min_n < 0 or max_n < min_n:
                raise ValueError("invalid choose min/max")

            n = rng.randint(min_n, max_n)
            picks = rng.sample(names, n)
            chosen_blocks.extend(picks)
            for b in picks:
                out_lines.extend(load_block_lines_from_db(b))  # <-- you provide this
            continue

        raise ValueError(f"unknown plan item keys: {list(item.keys())}")

    images = TheFactory.load_images("images.csv")
    lines, delays = TheFactory.extract_delays(out_lines)
    lines = TheFactory.assign_images(lines, images)
    steps = TheFactory.wrap_output(lines, delays)
    TheFactory.ensure_timer_s_everywhere(steps)
    steps = TheFactory.apply_effect_scoping(steps)

    return steps, chosen_blocks

def compile_script_to_steps(script_text: str) -> list[dict]:
    """
    Takes a raw script (multi-line text), applies TheFactory delay extraction and wrapping,
    returns steps list suitable for session_start.
    """
    text = normalize_newlines(script_text or "")
    lines = text.splitlines()

    images = TheFactory.load_images("images.csv")
    lines, delays = TheFactory.extract_delays(lines)
    lines = TheFactory.assign_images(lines, images)
    steps = TheFactory.wrap_output(lines, delays)
    TheFactory.ensure_timer_s_everywhere(steps)
    steps = TheFactory.apply_effect_scoping(steps)
    return steps

def compile_plan_to_script_lines(plan_obj: dict) -> tuple[list[str], list[str]]:
    """
    Returns (script_lines, chosen_blocks) without wrapping into steps.
    """
    if not isinstance(plan_obj, dict):
        raise ValueError("plan must be an object")
    items = plan_obj.get("plan")
    if not isinstance(items, list):
        raise ValueError("plan.plan must be a list")

    seed = plan_obj.get("seed", None)
    rng = random.Random(seed)

    chosen_blocks: list[str] = []
    out_lines: list[str] = []

    for item in items:
        if not isinstance(item, dict):
            continue

        if "include" in item:
            title = str(item["include"])
            chosen_blocks.append(title)
            out_lines.extend(load_block_lines_from_db(title))
            out_lines.append("")
            continue

        if "lines" in item:
            lines = item["lines"]
            if isinstance(lines, list):
                out_lines.extend([str(x) for x in lines])
                out_lines.append("")
            continue

        if "choose" in item and isinstance(item["choose"], dict):
            frm = item["choose"].get("from") or []
            frm = [str(x) for x in frm]
            min_n = int(item["choose"].get("min", 1))
            max_n = int(item["choose"].get("max", min_n))
            max_n = min(max_n, len(frm))
            n = rng.randint(min_n, max_n)
            picks = rng.sample(frm, n)
            chosen_blocks.extend(picks)
            for t in picks:
                out_lines.extend(load_block_lines_from_db(t))
                out_lines.append("")
            continue

    return out_lines, chosen_blocks

def extract_referenced_blocks_from_plan(plan_obj: dict) -> list[str]:
    """
    Returns a deduped, stable list of block titles referenced by include + choose.from.
    We intentionally include *all* choose candidates so the client can compile locally later.
    """
    items = (plan_obj or {}).get("plan")
    if not isinstance(items, list):
        return []

    referenced: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue

        if "include" in item:
            referenced.add(str(item["include"]).strip())

        elif "choose" in item and isinstance(item["choose"], dict):
            frm = item["choose"].get("from")
            if isinstance(frm, list):
                for x in frm:
                    referenced.add(str(x).strip())

    # remove empties and return sorted for deterministic order
    return sorted([t for t in referenced if t])

async def push_or_queue_session_with_blocks(device_id: str, session_title: str) -> None:
    # 1) Load session plan_json directly (same query build_inject_session_payload uses)
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT plan_json FROM sessions WHERE title = ?",
            (session_title,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Unknown session: {session_title}")

    plan_obj = _json_loads(row[0]) or {}
    block_titles = extract_referenced_blocks_from_plan(plan_obj)

    # 2) Inject all blocks first
    for bt in block_titles:
        payload_block = build_inject_block_payload(bt)
        await push_or_queue_injection(device_id=device_id, payload=payload_block)

    # 3) Then inject the session itself
    payload_session = build_inject_session_payload(session_title)
    await push_or_queue_injection(device_id=device_id, payload=payload_session)
    
SESSION_TAG_EXCLUDE = {"induction", "deepener", "training", "dream", "ending"}

def compute_session_meta_from_plan(plan: dict) -> tuple[list[str], int | None]:
    """
    Returns (tags, intensity_max) based on blocks referenced in the plan.
    For choose groups, we use *all* candidates in choose.from (max-of-possible).
    """
    if not isinstance(plan, dict):
        raise ValueError("plan must be an object")

    items = plan.get("plan")
    if not isinstance(items, list):
        raise ValueError("plan.plan must be a list")

    # Gather referenced block titles
    referenced: set[str] = set()

    for item in items:
        if not isinstance(item, dict):
            continue
        if "include" in item:
            referenced.add(str(item["include"]))
        elif "choose" in item and isinstance(item["choose"], dict):
            frm = item["choose"].get("from")
            if isinstance(frm, list):
                for x in frm:
                    referenced.add(str(x))

    # Pull tags + intensities from DB
    tags_union: set[str] = set()
    intensity_max: int | None = None

    with sqlite3.connect(DB_PATH) as conn:
        for title in sorted(referenced):
            row = conn.execute(
                "SELECT tags_json, intensity FROM blocks WHERE title = ?",
                (title,),
            ).fetchone()
            if not row:
                # Let compile fail later; but saving should also fail loudly
                raise KeyError(f"Unknown block: {title}")

            tags = _json_loads(row[0]) or []
            if isinstance(tags, list):
                for t in tags:
                    tt = (str(t) or "").strip().lower()
                    if tt and tt not in SESSION_TAG_EXCLUDE:
                        tags_union.add(tt)

            if row[1] is not None:
                try:
                    val = int(row[1])
                    intensity_max = val if intensity_max is None else max(intensity_max, val)
                except Exception:
                    pass

    return (sorted(tags_union), intensity_max)

def queue_delivery(device_id: str, payload: dict) -> None:
    now = int(time.time())
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO pending_deliveries (device_id, payload_json, created_at) VALUES (?, ?, ?)",
            (device_id, _json_dumps(payload), now),
        )
        conn.commit()
        
def catalogue_upsert_session(title: str, audience: str, payload: dict) -> None:
    now = int(time.time())
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
        INSERT INTO broadcast_catalogue_sessions (title, audience, payload_json, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(title) DO UPDATE SET
            audience=excluded.audience,
            payload_json=excluded.payload_json,
            updated_at=excluded.updated_at
        """, (title, audience, _json_dumps(payload), now))
        conn.commit()

def catalogue_upsert_block(title: str, audience: str, payload: dict) -> None:
    now = int(time.time())
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
        INSERT INTO broadcast_catalogue_blocks (title, audience, payload_json, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(title) DO UPDATE SET
            audience=excluded.audience,
            payload_json=excluded.payload_json,
            updated_at=excluded.updated_at
        """, (title, audience, _json_dumps(payload), now))
        conn.commit()
            
def get_catalogue_manifest(tier: str) -> dict:
    audiences = ["all", "paid"] if tier == "paid" else ["all"]
    placeholders = ",".join("?" * len(audiences))

    with sqlite3.connect(DB_PATH) as conn:
        session_rows = conn.execute(f"""
            SELECT title, updated_at FROM broadcast_catalogue_sessions
            WHERE audience IN ({placeholders})
            ORDER BY title COLLATE NOCASE
        """, audiences).fetchall()

        block_rows = conn.execute(f"""
            SELECT title, updated_at FROM broadcast_catalogue_blocks
            WHERE audience IN ({placeholders})
            ORDER BY title COLLATE NOCASE
        """, audiences).fetchall()

    return {
        "sessions": [{"title": r[0], "updated_at": r[1]} for r in session_rows],
        "blocks": [{"title": r[0], "updated_at": r[1]} for r in block_rows],
    }

def get_catalogue_session_payload(title: str) -> dict | None:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT payload_json FROM broadcast_catalogue_sessions WHERE title = ?",
            (title,)
        ).fetchone()
    return _json_loads(row[0]) if row else None

def get_catalogue_block_payload(title: str) -> dict | None:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT payload_json FROM broadcast_catalogue_blocks WHERE title = ?",
            (title,)
        ).fetchone()
    return _json_loads(row[0]) if row else None

def build_inject_block_payload(title: str) -> dict:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT title, summary, tags_json, intensity, body FROM blocks WHERE title = ?",
            (title,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Unknown block: {title}")

    _title, summary, tags_json, intensity, body = row
    tags = _json_loads(tags_json) or []
    return {
        "type": "inject_block",
        "title": _title,
        "summary": summary or "",
        "tags": tags if isinstance(tags, list) else [],
        "intensity": intensity,
        "body": body or "",
        "updated_at": int(time.time()), 
    }

def build_inject_session_payload(title: str) -> dict:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT title, summary, tags_json, intensity, plan_json FROM sessions WHERE title = ?",
            (title,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Unknown session: {title}")

    _title, summary, tags_json, intensity, plan_json = row
    tags = _json_loads(tags_json) or []
    plan_obj = _json_loads(plan_json) or {}

    session_json = {
        "name": _title,
        "plan": plan_obj.get("plan") or [],
        "seed": plan_obj.get("seed", None),
    }

    return {
        "type": "inject_session",
        "title": _title,
        "summary": summary or "",
        "tags": tags if isinstance(tags, list) else [],
        "intensity": intensity,
        "session_json": session_json,
        "updated_at": int(time.time()), 
    }

def resolve_target_device_ids(*, target: str, device_ids_csv: str | None = None) -> list[str]:
    """
    target:
      - "device": use device_ids_csv (1+ ids)
      - "all": all known devices
      - "paid": devices where tier == 'paid'
    """
    t = (target or "").strip().lower()

    if t == "device":
        ids = [x.strip() for x in (device_ids_csv or "").split(",") if x.strip()]
        if not ids:
            raise HTTPException(status_code=400, detail="device_ids is required when target=device")
        return ids

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        if t == "all":
            rows = conn.execute("SELECT device_id FROM devices").fetchall()
            return [r["device_id"] for r in rows]

        if t == "paid":
            rows = conn.execute("SELECT device_id FROM devices WHERE tier='paid'").fetchall()
            return [r["device_id"] for r in rows]

    raise HTTPException(status_code=400, detail="target must be one of: device, all, paid")
    
async def push_or_queue_injection(*, device_id: str, payload: dict) -> None:
    """
    If online -> send immediately.
    If offline -> enqueue in pending_deliveries (your Step 1–6 work).
    """
    if device_id in hub.connections:
        ws = hub.connections[device_id]
        await ws.send_text(json.dumps(payload))
        hub.log(device_id, "sent", detail=f"injection {payload.get('type')}", command_id=payload.get("id"))
        return

    # offline: queue it (replace enqueue_pending_delivery with your actual helper)
    queue_delivery(device_id=device_id, payload=payload)
    hub.log(device_id, "queued", detail=f"injection {payload.get('type')}", command_id=payload.get("id"))

@admin_router.post("/enroll/create")
def admin_create_enroll_code(ttl_minutes: int = 15):
    ttl_seconds = ttl_minutes * 60
    code, expires_at = create_enroll_code(ttl_seconds=ttl_seconds)
    return {"code": code, "expires_at": expires_at}

@app.post("/discord/enroll")
def enroll_discord(
    payload: dict = Body(default={})
):
    # Caddy basic_auth gates this route; no FastAPI auth needed.
    # Optional: validate payload shape if you want, but not required for code generation.
    code, expires_at = create_enroll_code(ttl_seconds=15 * 60)
    return JSONResponse({"code": code, "expires_at": expires_at})

@admin_router.get("/devices")
def admin_list_devices():
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT device_id, username, device_name, tier, last_seen FROM devices ORDER BY last_seen DESC",
        ).fetchall()

    return {
        "devices": [
            {
                "device_id": r[0],
                "username": r[1],
                "device_name": r[2],
                "tier": (r[3] or "free"),
                "last_seen": r[4],
            }
            for r in rows
        ]
    }

@admin_router.post("/device/{device_id}/tier")
async def admin_set_tier(device_id: str, tier: str = Form(...)):
    try:
        set_device_tier(device_id, tier)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    pushed = await push_tier_update(device_id)
    return {
        "ok": True,
        "device_id": device_id,
        "tier": tier.strip().lower(),
        "pushed_live": pushed,
    }

@admin_router.get("/blocks")
def admin_blocks_list():
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("""
            SELECT title, summary, tags_json, intensity
            FROM blocks
            ORDER BY title COLLATE NOCASE
        """).fetchall()

    blocks = []
    for (title, summary, tags_json, intensity) in rows:
        tags = _json_loads(tags_json) or []
        if not isinstance(tags, list):
            tags = []
        blocks.append({
            "title": title,
            "summary": summary or "",
            "tags": tags,
            "intensity": intensity,
        })
    return {"blocks": blocks}

@admin_router.get("/blocks/preview")
def admin_blocks_preview(title: str):
    title = (title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")

    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT body FROM blocks WHERE title = ?",
            (title,),
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"Unknown block: {title}")

    body = normalize_newlines(row[0] or "")
    return PlainTextResponse(body)

@admin_router.post("/blocks/save")
def admin_blocks_save(
    title: str = Form(...),
    summary: str = Form(""),
    tags: str = Form(""),
    intensity: str = Form(""),
    body: str = Form(...),
    overwrite: str = Form("0"),
):
    overwrite_flag = overwrite.strip() in ("1", "true", "yes", "on")
    tag_list = normalize_tags_csv(tags)

    intensity_val: int | None = None
    if intensity.strip() != "":
        try:
            intensity_val = int(intensity.strip())
        except Exception:
            return PlainTextResponse("intensity must be an integer (or blank).", status_code=400)

    try:
        _created, _overwritten = upsert_block(
            title=title,
            summary=summary,
            tags=tag_list,
            intensity=intensity_val,
            body=body,
            overwrite=overwrite_flag,
        )
    except HTTPException as e:
        # For HTMX confirmation UX, return plain text that includes a hint + keep 409
        return PlainTextResponse(str(e.detail), status_code=e.status_code)

    return PlainTextResponse(f"Saved block: {title}")

@admin_router.post("/blocks/bulk_upload")
async def admin_blocks_bulk_upload(
    csv_file: UploadFile = File(...),
    txt_files: list[UploadFile] = File(...),
    overwrite: str = Form("0"),
):
    overwrite_flag = overwrite.strip() in ("1", "true", "yes", "on")

    # 1) read CSV
    csv_raw = (await csv_file.read()).decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(csv_raw))
    required = {"title", "summary", "tags", "intensity"}
    if not required.issubset(set([h.strip() for h in (reader.fieldnames or [])])):
        return PlainTextResponse(f"CSV must contain columns: {sorted(required)}", status_code=400)

    meta_by_title: dict[str, dict] = {}
    filename_to_title: dict[str, str] = {}

    for row in reader:
        title = (row.get("title") or "").strip()
        if not title:
            continue

        meta_by_title[title] = {
            "summary": (row.get("summary") or "").strip(),
            "tags": normalize_tags_csv(row.get("tags") or ""),
            "intensity": (row.get("intensity") or "").strip(),
        }

        fn = (row.get("filename") or "").strip()
        if fn:
            filename_to_title[fn] = title

    # 2) preflight duplicates (so we can return a single confirmation list)
    duplicates: list[str] = []
    for f in txt_files:
        original_name = (f.filename or "").strip()
        stem = Path(original_name).stem
        title = filename_to_title.get(original_name) or stem

        if title and block_exists(title):
            duplicates.append(title)

    if duplicates and not overwrite_flag:
        dup_txt = "\n".join(f"- {t}" for t in sorted(set(duplicates)))
        return PlainTextResponse(
            "Duplicate titles detected (will overwrite existing blocks):\n"
            f"{dup_txt}\n\nRe-submit with overwrite=1 to confirm.",
            status_code=409,
        )

    # 3) ingest
    saved = 0
    skipped = 0
    errors: list[str] = []

    for f in txt_files:
        original_name = (f.filename or "").strip()
        stem = Path(original_name).stem
        title = filename_to_title.get(original_name) or stem

        meta = meta_by_title.get(title)
        if meta is None:
            skipped += 1
            continue

        body = (await f.read()).decode("utf-8", errors="replace")

        intensity_val: int | None = None
        if meta["intensity"] != "":
            try:
                intensity_val = int(meta["intensity"])
            except Exception:
                errors.append(f"{title}: bad intensity '{meta['intensity']}'")
                continue

        try:
            upsert_block(
                title=title,
                summary=meta["summary"],
                tags=meta["tags"],
                intensity=intensity_val,
                body=body,
                overwrite=True,  # bulk path already confirmed overwrite behavior
            )
            saved += 1
        except Exception as e:
            errors.append(f"{title}: {repr(e)}")

    # 4) response
    msg = f"Bulk upload complete. saved={saved} skipped_no_csv_row={skipped} errors={len(errors)}"
    if errors:
        msg += "\n\nErrors:\n" + "\n".join(errors[:50])
    return PlainTextResponse(msg)

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

def get_device_tier(device_id: str) -> str:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT tier FROM devices WHERE device_id = ?",
            (device_id,),
        ).fetchone()
    return (row[0] if row and row[0] else "free").strip().lower()

def set_device_tier(device_id: str, tier: str) -> None:
    tier = (tier or "").strip().lower()
    if tier not in ("free", "paid"):
        raise ValueError("tier must be 'free' or 'paid'")

    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "UPDATE devices SET tier = ? WHERE device_id = ?",
            (tier, device_id),
        )
        conn.commit()

    if cur.rowcount != 1:
        raise ValueError(f"Unknown device_id: {device_id}")

def require_paid(device_id: str) -> None:
    tier = get_device_tier(device_id)
    if tier != "paid":
        # 402 is “Payment Required” (rarely used, but semantically correct).
        # 403 is also reasonable. Pick one and stay consistent.
        raise HTTPException(status_code=402, detail="This feature requires a paid tier.")

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
    
@dataclass
class ActiveSession:
    session_id: str
    device_id: str
    started_at: float
    estimated_s: float

    def progress(self) -> float:
        elapsed = time.time() - self.started_at
        if self.estimated_s <= 0:
            return 1.0
        return min(1.0, elapsed / self.estimated_s)

    def elapsed_s(self) -> float:
        return time.time() - self.started_at

    def remaining_s(self) -> float:
        return max(0.0, self.estimated_s - self.elapsed_s())

class Hub:
    def __init__(self):
        # device_id -> WebSocket
        self.connections: Dict[str, WebSocket] = {}

        # device_id -> DeviceInfo
        self.devices: Dict[str, DeviceInfo] = {}

        # simple event log (in memory for PoC)
        self.logs: list[LogEvent] = []
        
        self.active_sessions: dict[str, ActiveSession] = {}
        
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
        self.clear_session(device_id)
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
            
    def record_session_start(self, device_id: str, session_id: str, estimated_s: float, started_at: float) -> None:
        self.active_sessions[device_id] = ActiveSession(
            session_id=session_id,
            device_id=device_id,
            started_at=started_at,
            estimated_s=estimated_s,
        )

    def clear_session(self, device_id: str) -> None:
        self.active_sessions.pop(device_id, None)

    def handle_ack(self, device_id: str, msg: dict):
        # Expect: {type:"ack", id:"cmd_x", status:"shown", detail:""}
        cmd_id = str(msg.get("id", "-"))
        status = str(msg.get("status", "ack"))
        detail = str(msg.get("detail", ""))
        
        self.log(device_id=device_id, event="ack", detail=f"{status} {detail}".strip(), command_id=cmd_id)

            
hub = Hub()

async def push_tier_update(device_id: str) -> bool:
    """
    If the device is currently connected, send a live tier update.
    Returns True if pushed, False if device is offline.
    """
    ws = hub.connections.get(device_id)
    if ws is None:
        return False

    tier = get_device_tier(device_id)
    await ws.send_text(json.dumps({
        "type": "tier",
        "tier": tier,
    }))

    hub.log(device_id, "sent", detail=f"tier {tier}", command_id="tier")
    return True

@app.get("/devices")
def get_devices():
    items = []
    for d in hub.devices.values():
        last_seen_ts = as_ts(d.last_seen)
        items.append({
            "device_id": d.device_id,
            "username": d.username,
            "device_name": d.device_name,
            "version": d.version,
            "connected_at": d.connected_at,
            "last_seen_ts": last_seen_ts,
            "last_seen": fmt_unix_et(last_seen_ts),
            "online": d.device_id in hub.connections,
            "tier": get_device_tier(d.device_id),
        })

    # Sort: online first, then most recently seen
    items.sort(key=lambda x: (not x["online"], -x["last_seen_ts"]))
    return {"devices": items}

@app.get("/device/{device_id}", response_class=HTMLResponse)
def device_page(request: Request, device_id: str):
    d = hub.devices.get(device_id)
    sess = hub.active_sessions.get(d.device_id)
    if not d:
        raise HTTPException(status_code=404, detail="Unknown device")
    last_seen_ts = as_ts(d.last_seen)
    device = {
        "device_id": d.device_id,
        "username": d.username,
        "device_name": d.device_name,
        "version": d.version,
        "connected_at": d.connected_at,
        "last_seen_ts": last_seen_ts,
        "last_seen": fmt_unix_et(last_seen_ts),
        "online": d.device_id in hub.connections,
        "tier": get_device_tier(d.device_id),
        "session_progress": round(sess.progress() * 100) if sess else None,
        "session_remaining_s": round(sess.remaining_s()) if sess else None,
    }

    logs = [l for l in hub.logs if l.device_id == device_id][-50:]

    return templates.TemplateResponse(
        "device.html",
        {"request": request, "device": device, "logs": logs, "sessions": list_sessions(), "blocks": list_blocks()},
    )

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    devices = []
    for d in hub.devices.values():
        last_seen_ts = as_ts(d.last_seen)
        sess = hub.active_sessions.get(d.device_id)
        devices.append({
            "device_id": d.device_id,
            "username": d.username,
            "device_name": d.device_name,
            "version": d.version,
            "connected_at": d.connected_at,
            "last_seen_ts": last_seen_ts,            # NEW
            "last_seen": fmt_unix_et(last_seen_ts),  # display string
            "online": d.device_id in hub.connections,
            "session_progress": round(sess.progress() * 100) if sess else None,
            "session_remaining_s": round(sess.remaining_s()) if sess else None,
        })

    devices.sort(key=lambda x: (not x["online"], -x["last_seen_ts"]))
    return templates.TemplateResponse("index.html", {"request": request, "devices": devices, "sessions": list_sessions(), "blocks": list_blocks()})

@app.get("/sessions", response_class=HTMLResponse)
def sessions_page(request: Request, load: str = ""):
    sessions = list_sessions()
    blocks = list_blocks()

    loaded = {
        "title": "",
        "summary": "",
        "tags": "",
        "intensity": "",
        "plan_json": json.dumps({"plan": []}, indent=2, ensure_ascii=False),
    }

    if load.strip():
        try:
            plan = load_session_plan(load.strip())
            loaded["title"] = load.strip()
            loaded["plan_json"] = json.dumps(plan, indent=2, ensure_ascii=False)

            meta = next((s for s in sessions if s["title"] == loaded["title"]), None)
            if meta:
                loaded["summary"] = meta.get("summary") or ""
                loaded["tags"] = ", ".join(meta.get("tags") or [])
                loaded["intensity"] = "" if meta.get("intensity") is None else str(meta["intensity"])
        except Exception:
            pass

    return templates.TemplateResponse(
        "sessions.html",
        {
            "request": request,
            "sessions": sessions,
            "blocks": blocks,
            "loaded": loaded,
        },
    )

@app.get("/admin/blocks/page", response_class=HTMLResponse)
def blocks_page(request: Request):
    return templates.TemplateResponse(
        "blocks.html",
        {"request": request, "blocks": list_blocks()},
    )

@app.post("/sessions/preview")
def sessions_preview(plan_json: str = Form(...)):
    try:
        plan_obj = json.loads(plan_json)
    except Exception as e:
        return PlainTextResponse(f"Invalid JSON: {e}", status_code=400)

    try:
        steps, chosen_blocks = compile_plan_to_steps(plan_obj)
    except KeyError as e:
        return PlainTextResponse(str(e), status_code=404)
    except Exception as e:
        return PlainTextResponse(f"Compile failed: {e}", status_code=400)

    return PlainTextResponse(
        "OK\n"
        f"steps={len(steps)}\n"
        f"blocks_used={len(chosen_blocks)}\n"
        f"first_blocks={chosen_blocks[:8]}\n\n"
        "first_10_steps:\n"
        + json.dumps(steps[:10], indent=2, ensure_ascii=False)
    )

@app.post("/sessions/save")
def sessions_save(
    title: str = Form(...),
    summary: str = Form(""),
    tags: str = Form(""),
    intensity: str = Form(""),
    plan_json: str = Form(...),
    overwrite: str = Form("0"),
):
    overwrite_flag = overwrite.strip().lower() in ("1", "true", "yes", "on")

    try:
        plan_obj = json.loads(plan_json)
    except Exception as e:
        return PlainTextResponse(f"Invalid JSON: {e}", status_code=400)

    try:
        computed_tags, computed_intensity = compute_session_meta_from_plan(plan_obj)
    except KeyError as e:
        return PlainTextResponse(str(e), status_code=404)
    except Exception as e:
        return PlainTextResponse(f"Meta compute failed: {e}", status_code=400)

    try:
        upsert_session(
            title=title,
            summary=summary,
            tags=computed_tags,
            intensity=computed_intensity,
            plan=plan_obj,
            overwrite=overwrite_flag,
        )
    except HTTPException as e:
        return PlainTextResponse(str(e.detail), status_code=e.status_code)
    except Exception as e:
        return PlainTextResponse(f"Save failed: {e}", status_code=400)

    return PlainTextResponse(f"Saved session: {title}")

@app.get("/device/{device_id}/session_progress", response_class=HTMLResponse)
def session_progress_fragment(device_id: str):
    sess = hub.active_sessions.get(device_id)
    if sess is None:
        return HTMLResponse("")  # empty = nothing to show
    progress = round(sess.progress() * 100)
    remaining = round(sess.remaining_s())
    return HTMLResponse(f"""
        <b>Session in progress</b> — ~{remaining}s remaining
        <div style="width:100%; max-width:400px; background:#ddd; border-radius:4px; overflow:hidden; margin-top:6px;">
            <div style="width:{progress}%; background:#4b006e; height:16px; transition: width 2s linear;"></div>
        </div>
    """)

@app.post("/device/{device_id}/tier")
async def set_tier_from_device_page(
    device_id: str,
    tier: str = Form(...),
):
    try:
        set_device_tier(device_id, tier)
    except ValueError as e:
        return PlainTextResponse(str(e), status_code=400)

    pushed = await push_tier_update(device_id)
    suffix = " (pushed live)" if pushed else " (device offline)"
    return PlainTextResponse(f"Tier set to: {tier.strip().lower()}{suffix}")

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

@app.post("/device/{device_id}/session/start_saved")
async def session_start_saved_htmx(
    device_id: str,
    session_title: str = Form(...),
):
    if device_id not in hub.connections:
        return PlainTextResponse("Device is offline (no active connection).", status_code=409)

    session_title = (session_title or "").strip()
    if not session_title:
        return PlainTextResponse("session_title is required.", status_code=400)

    try:
        plan = load_session_plan(session_title)
        steps, chosen_blocks = compile_plan_to_steps(plan)
    except KeyError:
        return PlainTextResponse(f"Unknown session: {session_title}", status_code=404)
    except Exception as e:
        return PlainTextResponse(f"Compile failed: {e}", status_code=400)

    cmd_id = f"cmd_{uuid.uuid4().hex[:10]}"
    session_id = f"sess_{uuid.uuid4().hex[:10]}"

    # right after you compute `steps`
    meta = get_session_meta_by_title(session_title) or {}

    payload = {
        "type": "session_start",
        "id": cmd_id,
        "session_id": f"sess_{uuid.uuid4().hex[:10]}",
        "body": steps,
        "title": meta.get("title", session_title),
        "summary": meta.get("summary", ""),
        "tags": meta.get("tags", []),
        "intensity": meta.get("intensity", None),
        "blocks": chosen_blocks,
    }

    ws = hub.connections[device_id]
    await ws.send_text(json.dumps(payload))

    hub.log(
        device_id,
        "sent",
        detail=f"session_start_saved title={session_title} steps={len(steps)} blocks={len(chosen_blocks)}",
        command_id=cmd_id,
    )

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

@app.post("/device/{device_id}/sessiongen/from_blocks")
def sessiongen_from_blocks(
    device_id: str,
    block_titles: str = Form(""),
):
    titles = [t.strip() for t in (block_titles or "").split(",") if t.strip()]
    if not titles:
        return PlainTextResponse("", status_code=400)

    out_lines: list[str] = []
    for title in titles:
        out_lines.extend(load_block_lines_from_db(title))
        out_lines.append("")  # spacer line between blocks

    return PlainTextResponse("\n".join(out_lines).strip() + "\n")

@app.post("/device/{device_id}/sessiongen/from_saved_session")
def sessiongen_from_saved_session(
    device_id: str,
    session_title: str = Form(...),
):
    plan = load_session_plan(session_title.strip())
    lines, _chosen = compile_plan_to_script_lines(plan)
    return PlainTextResponse("\n".join(lines).strip() + "\n")

@app.post("/device/{device_id}/sessiongen/send")
async def sessiongen_send(
    device_id: str,
    title: str = Form("Live Session"),
    script_text: str = Form(...),
):
    if device_id not in hub.connections:
        return PlainTextResponse("Device is offline (no active connection).", status_code=409)

    steps = compile_script_to_steps(script_text)

    cmd_id = f"cmd_{uuid.uuid4().hex[:10]}"
    session_id = f"sess_{uuid.uuid4().hex[:10]}"

    payload = {
        "type": "session_start",
        "id": cmd_id,
        "session_id": session_id,
        "body": steps,

        # metadata for dialog gating on client
        "title": (title or "Live Session").strip(),     
        "summary": "",
        "tags": [],
        "intensity": None,
        "blocks": [],
    }

    ws = hub.connections[device_id]
    await ws.send_text(json.dumps(payload))

    hub.log(device_id, "sent", detail=f"sessiongen_send steps={len(steps)}", command_id=cmd_id)
    return PlainTextResponse(f"Sent {cmd_id} ({len(steps)} steps)")

@app.post("/device/{device_id}/inject/block")
async def inject_block_to_device(device_id: str, block_title: str = Form(...)):
    payload = build_inject_block_payload(block_title.strip())

    await push_or_queue_injection(device_id=device_id, payload=payload)
    return PlainTextResponse("OK inject_session → device")

@app.post("/device/{device_id}/inject/session")
async def inject_session_to_device(device_id: str, session_title: str = Form(...)):
    await push_or_queue_session_with_blocks(device_id, session_title.strip())
    return PlainTextResponse("OK inject_session (+blocks) → device")

@app.post("/inject/block")
async def inject_block_broadcast(
    target: str = Form(...),
    block_title: str = Form(...),
    device_ids: str = Form(""),
):
    targets = resolve_target_device_ids(target=target, device_ids_csv=device_ids)
    payload = build_inject_block_payload(block_title.strip())

    if target.strip().lower() in ("all", "paid"):
        catalogue_upsert_block(block_title.strip(), target.strip().lower(), payload)

    for did in targets:
        await push_or_queue_injection(device_id=did, payload=payload)

    return PlainTextResponse(f"OK inject_block -> {len(targets)} target(s)")

@app.post("/inject/session")
async def inject_session_broadcast(
    target: str = Form(...),
    session_title: str = Form(...),
    device_ids: str = Form(""),
):
    targets = resolve_target_device_ids(target=target, device_ids_csv=device_ids)
    payload = build_inject_session_payload(session_title.strip())

    if target.strip().lower() in ("all", "paid"):
        catalogue_upsert_session(session_title.strip(), target.strip().lower(), payload)

    for did in targets:
        await push_or_queue_session_with_blocks(did, session_title.strip())

    return PlainTextResponse(f"OK inject_session -> {len(targets)} target(s)")

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    
    device_token = ws.query_params.get("device_token")
    enroll_code = ws.query_params.get("enroll_code")


    if (device_token is None) == (enroll_code is None):
        logging.warning("WS auth rejected: must supply exactly one of device_token or enroll_code")
        await ws.close(code=1008)
        return
    
    expected_device_id = None
    if device_token:
        expected_device_id = get_device_id_for_token(device_token)
        if not expected_device_id:
            logging.warning("WS auth rejected: unknown device_token")
            await ws.close(code=1008)
            return

    if enroll_code:
        if not consume_enroll_code(enroll_code):
            logging.warning("WS auth rejected: invalid or expired enroll_code")
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
        device_id = str(msg.get("device_id", "")).strip()[:64]
        username = str(msg.get("username", "")).strip()[:64]
        device_name = str(msg.get("device_name", "")).strip()[:64]
        version = str(msg.get("version", "v0.0")).strip()[:16]
        protocol = str(msg.get("protocol","v0.0")).strip()[:8]
        
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
        
        tier = get_device_tier(device_id)

        await ws.send_text(json.dumps({
            "type": "tier",
            "tier": tier,   # "free" or "paid"
        }))

        # 4) Keep the connection alive (we'll fill this in next chunk)
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)

            # Any message means the device is alive
            hub.update_last_seen(device_id)
            update_device_metadata(device_id, None, None)

            mtype = msg.get("type")

            if mtype == "heartbeat":
                tier = get_device_tier(device_id)
                manifest = get_catalogue_manifest(tier)
                await ws.send_text(json.dumps({
                    "type": "server_status",
                    "last_command_ts": hub.last_command_ts,
                    "catalogue": manifest,
                }))
                hub.update_last_seen(device_id)
                update_device_metadata(device_id, None, None)
            elif mtype == "ack":
                hub.handle_ack(device_id, msg)
            elif mtype == "catalogue_sync":
                tier = get_device_tier(device_id)
                allowed_audiences = ["all", "paid"] if tier == "paid" else ["all"]
                
                want_sessions = [str(t) for t in (msg.get("want_sessions") or [])]
                want_blocks = [str(t) for t in (msg.get("want_blocks") or [])]
                
                # send blocks first
                for title in want_blocks:
                    payload = get_catalogue_block_payload(title)
                    if payload is None:
                        continue
                    # verify audience
                    with sqlite3.connect(DB_PATH) as conn:
                        row = conn.execute(
                            "SELECT audience FROM broadcast_catalogue_blocks WHERE title = ?",
                            (title,)
                        ).fetchone()
                    if row and row[0] in allowed_audiences:
                        await ws.send_text(json.dumps(payload))
                
                # then sessions (blocks first within each session)
                for title in want_sessions:
                    payload = get_catalogue_session_payload(title)
                    if payload is None:
                        continue
                    with sqlite3.connect(DB_PATH) as conn:
                        row = conn.execute(
                            "SELECT audience FROM broadcast_catalogue_sessions WHERE title = ?",
                            (title,)
                        ).fetchone()
                    if row and row[0] in allowed_audiences:
                        plan_obj = payload.get("session_json") or {}
                        for bt in extract_referenced_blocks_from_plan(plan_obj):
                            block_payload = get_catalogue_block_payload(bt)
                            if block_payload:
                                await ws.send_text(json.dumps(block_payload))
                        await ws.send_text(json.dumps(payload))
            elif mtype == "session_started":
                estimated_s = float(msg.get("estimated_s") or 0)
                started_at = float(msg.get("started_at") or time.time())
                session_id = str(msg.get("session_id") or "")
                hub.record_session_start(device_id, session_id, estimated_s, started_at)
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
