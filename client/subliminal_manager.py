# subliminal_manager.py
from __future__ import annotations
import random
import time
from dataclasses import dataclass
from typing import Sequence, Optional

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import QApplication, QLabel, QDialog
from PySide6.QtGui import QFont, QScreen
from ui_settings import get_popup_screens

@dataclass
class SubliminalState:
    active: bool = False
    end_ts: float | None = None


class SubliminalManager(QObject):
    def __init__(self):
        super().__init__()
        self.state = SubliminalState()
        self._timer: Optional[QTimer] = None
        self._dlg: Optional[QDialog] = None
        self._label: Optional[QLabel] = None

    def start(
        self,
        messages: Sequence[str],
        *,
        duration_s: float | None = None,
        interval_ms: int = 50,
        flash_ms: int = 16,
        font_pt: int = 18,
    ) -> None:
        msgs = [m.strip() for m in (messages or []) if m and m.strip()]
        if not msgs:
            raise ValueError("subliminal_start requires non-empty messages list")

        self.stop()

        self._dlg = QDialog()
        self._dlg.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool
        )
        self._dlg.setAttribute(Qt.WA_TranslucentBackground)
        self._dlg.setAttribute(Qt.WA_ShowWithoutActivating)

        self._label = QLabel(self._dlg)

        f = QFont("Segoe UI")
        f.setPointSize(int(font_pt))
        f.setWeight(QFont.Medium)
        f.setItalic(True)
        self._label.setFont(f)

        self._label.setAlignment(Qt.AlignCenter)

# transparent background, deep purple text
        self._label.setStyleSheet("""
            QLabel {
                background: transparent;
                color: #4b006e;
            }
        """)

        self._dlg.resize(600, 120)

        end_ts = (time.time() + float(duration_s)) if duration_s is not None else None
        self.state = SubliminalState(active=True, end_ts=end_ts)

        def _flash_once():
            if not self.state.active:
                self.stop()
                return
            if self.state.end_ts is not None and time.time() >= self.state.end_ts:
                self.stop()
                return

            text = random.choice(msgs)
            self._label.setText(text)
            self._label.adjustSize()

            screens = QApplication.screens()
            allowed = get_popup_screens()
            if allowed:
                screens = [screens[i] for i in allowed if 0 <= i < len(screens)] or screens
            scr = random.choice(screens)
            geom = scr.availableGeometry()

            w = max(200, min(self._label.width() + 40, 700))
            h = max(60, min(self._label.height() + 30, 200))
            self._dlg.resize(w, h)

            x = geom.x() + random.randint(0, max(0, geom.width() - w))
            y = geom.y() + random.randint(0, max(0, geom.height() - h))
            self._dlg.move(x, y)

            self._dlg.show()
            self._dlg.raise_()

            QTimer.singleShot(int(flash_ms), self._dlg.hide)

        self._timer = QTimer(self)
        self._timer.timeout.connect(_flash_once)
        self._timer.start(int(interval_ms))

    def stop(self) -> None:
        self.state = SubliminalState()
        if self._timer is not None:
            self._timer.stop()
            self._timer.deleteLater()
            self._timer = None
        if self._dlg is not None:
            self._dlg.hide()
            self._dlg.deleteLater()
            self._dlg = None
            self._label = None
