from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtCore import QObject, QTimer
import re
import random
from ui_settings import get_pet_names
from TheFactory import DEFAULT_PACING_S
import time

_PNS_PATTERN = re.compile(r"#PNS", re.IGNORECASE)

DEFAULT_SESSION_TIMER_MS = int(DEFAULT_PACING_S * 1000)

def _estimate_duration_s(steps: list[dict]) -> float:
    return sum(
        max(0.0, float(s.get("timer_s", DEFAULT_SESSION_TIMER_MS / 1000)))
        for s in steps
        if isinstance(s, dict)
    )

def _apply_pns(text: str) -> str:
    names = get_pet_names() or []
    if not text or not names:
        return text

    matches = list(_PNS_PATTERN.finditer(text))
    n = len(matches)
    if n == 0:
        return text

    # Best-effort uniqueness per message:
    # - If we have enough unique names, use sampling without replacement for all occurrences.
    # - If not, use each name once (shuffled), then allow repeats for remaining occurrences.
    if len(names) >= n:
        picks = random.sample(names, k=n)
    else:
        picks = random.sample(names, k=len(names))
        last = picks[-1] if picks else None
        for _ in range(n - len(picks)):
            # try to avoid immediate repeats when we’re forced to repeat
            if len(names) > 1:
                for __ in range(5):
                    c = random.choice(names)
                    if c != last:
                        break
                else:
                    c = random.choice(names)
            else:
                c = names[0]
            picks.append(c)
            last = c

    it = iter(picks)
    return _PNS_PATTERN.sub(lambda _m: next(it), text)

def _apply_pns_to_step(step: dict) -> dict:
    step = dict(step)  # shallow copy

    for key in ("title", "body", "text", "url"):
        if isinstance(step.get(key), str):
            step[key] = _apply_pns(step[key])

    if isinstance(step.get("messages"), list):
        step["messages"] = [
            _apply_pns(m) if isinstance(m, str) else m
            for m in step["messages"]
        ]

    return step


DispatchFn = Callable[[dict], None]

class SessionRunner(QObject):
    """
    Runs sessions on the Qt thread using QTimer so nothing blocks the UI.
    """
    def __init__(self, dispatch: DispatchFn):
        super().__init__()
        self._dispatch = dispatch
        self._active = False
        self._session_id: str | None = None
        self._steps: list[dict] = []
        self._i = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._run_next_step)
        self._paused = False

    def pause(self) -> None:
        self._paused = True
        self._timer.stop()

    def resume(self) -> None:
        print("SessionRunner.resume called", "active=", self._active, "paused=", self._paused, "i=", self._i, "n=", len(self._steps))
        if not self._active:
            return
        self._paused = False
        # continue immediately
        self._timer.start(0)
        
    def is_paused(self) -> bool:
        return bool(self._paused)

    def toggle_pause(self) -> None:
        if not self._active:
            return
        if self._paused:
            self.resume()
        else:
            self.pause()


    def is_active(self) -> bool:
        return self._active

    def cancel(self) -> None:
        self._timer.stop()
        self._active = False
        self._paused = False
        self._session_id = None
        self._steps = []
        self._i = 0

    def start(self, session_id: str, steps: list[dict]) -> None:
        # Cancel any existing session for MVP simplicity
        self.cancel()

        self._active = True
        self._session_id = session_id
        self._steps = steps
        self._i = 0
        
        estimated_s = _estimate_duration_s(steps)
        self._dispatch({
            "type": "__session_started__",
            "session_id": session_id,
            "estimated_s": estimated_s,
            "started_at": time.time(),
        })

        self._run_next_step()

    def _run_next_step(self) -> None:
        if self._paused:
            return

        if not self._active:
            return

        if self._i >= len(self._steps):
            # Session done
            self.cancel()
            return

        step = dict(self._steps[self._i])  # shallow copy
        self._i += 1
        if isinstance(step, dict):
            step = _apply_pns_to_step(step)
        # Timer is session pacing only (Option A)
        timer_s = step.get("timer_s")
        if timer_s is None:
            # Apply session default pacing
            # Inject effective timer into the step so message lifespan derivation can use it.
            timer_s = DEFAULT_SESSION_TIMER_MS / 1000.0
            step["timer_s"] = timer_s

        # Dispatch the step immediately
        self._dispatch(step)

        try:
            delay_ms = int(float(timer_s) * 1000)
        except Exception:
            delay_ms = 0

        if delay_ms < 0:
            delay_ms = 0

        self._timer.start(delay_ms)
