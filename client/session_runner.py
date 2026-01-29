from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtCore import QObject, QTimer

DispatchFn = Callable[[dict], None]

DEFAULT_SESSION_TIMER_MS = 8000

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
        self._timer: QTimer | None = None

    def is_active(self) -> bool:
        return self._active

    def cancel(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer.deleteLater()
            self._timer = None
        self._active = False
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

        self._run_next_step()

    def _run_next_step(self) -> None:
        if not self._active:
            return

        if self._i >= len(self._steps):
            # Session done
            self.cancel()
            return

        step = dict(self._steps[self._i])  # shallow copy
        self._i += 1

        # Dispatch the step immediately
        self._dispatch(step)

        # Timer is session pacing only (Option A)
        timer_s = step.get("timer_s")
        if timer_s is None:
            # Apply session default pacing
            # Inject effective timer into the step so message lifespan derivation can use it.
            step["timer_s"] = DEFAULT_SESSION_TIMER_MS / 1000.0
            QTimer.singleShot(DEFAULT_SESSION_TIMER_MS, self._run_next_step)
            return


        try:
            delay_ms = int(float(timer_s) * 1000)
        except Exception:
            delay_ms = 0

        if delay_ms < 0:
            delay_ms = 0

        QTimer.singleShot(delay_ms, self._run_next_step)
