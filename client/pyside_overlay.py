from __future__ import annotations
import os
import tempfile
from dataclasses import dataclass
from typing import Optional

import requests
from PySide6.QtCore import Qt
from PySide6.QtGui import QMovie
from PySide6.QtWidgets import QApplication, QDialog, QLabel
import sys

if sys.platform.startswith("win"):
    import ctypes
    from ctypes import wintypes

    GWL_EXSTYLE = -20
    WS_EX_LAYERED = 0x00080000
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_NOACTIVATE = 0x08000000

    user32 = ctypes.WinDLL("user32", use_last_error=True)

    GetWindowLongW = user32.GetWindowLongW
    GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    GetWindowLongW.restype = ctypes.c_long

    SetWindowLongW = user32.SetWindowLongW
    SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    SetWindowLongW.restype = ctypes.c_long

    def _make_click_through(hwnd: int) -> None:
        ex = GetWindowLongW(hwnd, GWL_EXSTYLE)
        ex |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
else:
    def _make_click_through(hwnd: int) -> None:
        return

_ACTIVE_OVERLAYS: list["GifOverlay"] = []


def _download_to_temp(url: str, timeout_s: int = 10) -> str:
    r = requests.get(url, timeout=timeout_s)
    r.raise_for_status()

    fd, path = tempfile.mkstemp(suffix=".gif")
    os.close(fd)
    with open(path, "wb") as f:
        f.write(r.content)
    return path

def stop_gif_overlays() -> None:
    for dlg in list(_ACTIVE_OVERLAYS):
        try:
            dlg.close()
        except Exception:
            pass

class GifOverlay(QDialog):
    def __init__(
        self,
        *,
        screen_index: int,
        gif_path: str,
        opacity: float = 1.0,
        parent=None
    ):
        super().__init__(parent)

        self._gif_path = gif_path
        self._movie: Optional[QMovie] = None

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.Tool
            | Qt.WindowStaysOnTopHint
        )
        # Qt-level click-through (often enough)
        self.setWindowFlag(Qt.WindowTransparentForInput, True)

        # Don’t accept mouse events at the widget level either
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating,True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setWindowFlag(Qt.Tool, True)

        # Geometry = target monitor
        screens = QApplication.screens()
        if not screens:
            raise RuntimeError("No screens found")
        if screen_index < 0 or screen_index >= len(screens):
            raise ValueError(f"Invalid screen_index={screen_index}")

        geom = screens[screen_index].geometry()
        self.setGeometry(geom)

        self._label = QLabel(self)
        self._label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._label.setAttribute(Qt.WA_TranslucentBackground)
        self._label.setStyleSheet("background: transparent;")
        self._label.setGeometry(0, 0, geom.width(), geom.height())

        # Opacity on the whole window (simple MVP)
        self.setWindowOpacity(max(0.0, min(1.0, float(opacity))))

        self._movie = QMovie(self._gif_path)

# Every time a new frame is ready, redraw it stretched to the window
        self._movie.frameChanged.connect(lambda _i: self._render_scaled_frame())

        self._movie.start()

# Render first frame immediately
        self._render_scaled_frame()

        self._movie.start()
        
    def _render_scaled_frame(self) -> None:
        if self._movie is None:
            return
        pix = self._movie.currentPixmap()
        if pix.isNull():
            return

        scaled = pix.scaled(
            self._label.size(),
            Qt.IgnoreAspectRatio,          # <- stretch to fill
            Qt.SmoothTransformation,
        )
        self._label.setPixmap(scaled)


    def closeEvent(self, event):
        try:
            if self._movie is not None:
                self._movie.stop()
        finally:
            super().closeEvent(event)


def show_gif_overlay(url: str, *, screen: int = -1, opacity: float = 1.0) -> None:
    """
    screen:
      -1 = all screens
      >=0 = only that screen index
    """
    url = (url or "").strip()
    if not url:
        return

    gif_path = _download_to_temp(url)

    screens = QApplication.screens()
    if not screens:
        return

    target_indices = range(len(screens)) if screen == -1 else [screen]

    for idx in target_indices:
        dlg = GifOverlay(screen_index=idx, gif_path=gif_path, opacity=opacity)
        _ACTIVE_OVERLAYS.append(dlg)

        def _forget(d=dlg):
            if d in _ACTIVE_OVERLAYS:
                _ACTIVE_OVERLAYS.remove(d)

        dlg.destroyed.connect(_forget)
        dlg.show()
        _make_click_through(int(dlg.winId()))
        dlg.raise_()
