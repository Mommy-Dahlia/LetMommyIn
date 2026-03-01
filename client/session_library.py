# session_library.py
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class SessionInfo:
    key: str          # stable identifier, e.g. filename stem
    name: str         # display name
    path: Path

class SessionLibrary:
    def __init__(self, base_dir: Path):
        self.dir = base_dir
        self.dir.mkdir(parents=True, exist_ok=True)

    def list_sessions(self) -> list[SessionInfo]:
        out: list[SessionInfo] = []
        for p in sorted(self.dir.glob("*.json")):
            if p.name.endswith(".meta.json"):
                continue
            key = p.stem
            out.append(SessionInfo(key=key, name=key.replace("_", " ").title(), path=p))
        return out

    def load_steps(self, sess: SessionInfo) -> list[dict]:
        raw = sess.path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError("Session JSON must be a list of steps")
        for i, step in enumerate(data):
            if not isinstance(step, dict):
                raise ValueError(f"Step {i} must be an object")
            if "type" not in step:
                raise ValueError(f"Step {i} missing 'type'")
        return data