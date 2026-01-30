# wfm_manager.py
from __future__ import annotations
from typing import Callable, Optional
import traceback

from PySide6.QtCore import QObject, Signal

from pyside_show_writeforme import WriteForMommy


class WfmManager(QObject):
    """
    Owns the active WFM dialog and calls a continuation when completed.
    """
    def __init__(self):
        super().__init__()
        self._active_dialog: Optional[WriteForMommy] = None
        self._on_done: Optional[Callable[[], None]] = None

    def start(self, *, text: str, reps: int, on_done: Callable[[], None]) -> None:
        text = (text or "").strip()
        if not text:
            raise ValueError("write_for_me missing text")

        # Cancel any existing WFM (MVP policy)
        self.cancel()

        self._on_done = on_done
        dlg = WriteForMommy(text=text, targetreps=int(reps))
        self._active_dialog = dlg

        # When dialog closes, we consider it "done" only if it signaled completion.
        # We'll add a completion signal next.
        dlg.completed.connect(self._handle_completed)

    def _handle_completed(self) -> None:
        print("WFM completed signal received; calling on_done")
        cb = self._on_done
        self._on_done = None
        self.cancel(clear_callback=False)
        if cb:
            try:
                cb()
            except Exception as e:
                print("on_done callback failed:", repr(e))
                traceback.print_exc()

    def cancel(self, *, clear_callback: bool = True) -> None:
        if self._active_dialog is not None:
            try:
                self._active_dialog.close()
            except Exception:
                pass
        self._active_dialog = None
        if clear_callback:
            self._on_done = None

