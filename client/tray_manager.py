# tray.py
from __future__ import annotations

import time
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal, QTimer
from PySide6.QtGui import QIcon, QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QApplication

@dataclass
class TrayState:
    last_server_cmd_ts: float | None = None
    connected: bool = False

class TrayManager(QObject):
    """
    Lives on the Qt thread.
    Owns the tray icon + menu.
    """
    request_exit = Signal()
    # optional: emit when user changes settings
    screens_changed = Signal(object)   # payload: list[int] | None
    audio_device_changed = Signal(object)  # payload: str | None

    def __init__(
        self,
        *,
        icon_fresh: QIcon,
        icon_stale: QIcon,
        icon_offline: QIcon,
        get_screen_choices: Callable[[], list[tuple[str, object]]],
        get_audio_choices: Callable[[], list[tuple[str, object]]],
        get_selected_screens: Callable[[], list[int] | None],
        set_selected_screens: Callable[[list[int] | None], None],
        get_selected_audio: Callable[[], str | None],
        set_selected_audio: Callable[[str | None], None],
    ):
        super().__init__()
        self.state = TrayState()
        self._icon_fresh = icon_fresh
        self._icon_stale = icon_stale
        self._icon_offline = icon_offline

        self._get_screen_choices = get_screen_choices
        self._get_audio_choices = get_audio_choices

        self._get_selected_screens = get_selected_screens
        self._set_selected_screens = set_selected_screens
        self._get_selected_audio = get_selected_audio
        self._set_selected_audio = set_selected_audio

        self.tray = QSystemTrayIcon(self._icon_offline, QApplication.instance())
        self.tray.setToolTip("Let Mommy In")

        menu = QMenu()
        self._status_action = QAction("Time since last command: —", menu)
        self._status_action.setEnabled(False)   # non-selectable
        menu.addAction(self._status_action)
        menu.addSeparator()

        # Screens submenu
        self._screens_menu = menu.addMenu("Popup screens")
        self._rebuild_screens_menu()

        # Audio submenu
        self._audio_menu = menu.addMenu("Audio output")
        self._rebuild_audio_menu()

        menu.addSeparator()

        act_exit = QAction("Exit", menu)
        act_exit.triggered.connect(self.request_exit.emit)
        menu.addAction(act_exit)

        self.tray.setContextMenu(menu)
        self.tray.show()
        self._tick = QTimer(self)
        self._tick.timeout.connect(self.refresh_icon)
        self._tick.start(30_000)  # every 30 seconds

        # set initial icon state
        self.refresh_icon()

    def set_connected(self, connected: bool) -> None:
        self.state.connected = connected
        self.refresh_icon()

    def set_last_server_cmd_ts(self, ts: float | None) -> None:
        self.state.last_server_cmd_ts = ts
        self.refresh_icon()

    def _fmt_age(self, age_s: float) -> str:
        if age_s < 0:
            age_s = 0
        mins = int(age_s // 60)
        hrs = mins // 60
        mins = mins % 60
        if hrs > 0:
            return f"{hrs}h {mins:02d}m"
        return f"{mins}m"

    def refresh_icon(self) -> None:
        # default status text
        status_text = "Waiting for heartbeat to load"
        tip_lines = ["Let Mommy In"]

        if not self.state.connected:
            self.tray.setIcon(self._icon_offline)
            if hasattr(self, "_status_action"):
                self._status_action.setText(status_text)
            self.tray.setToolTip("\n".join(tip_lines))
            return

        ts = self.state.last_server_cmd_ts
        if ts is None:
            self.tray.setIcon(self._icon_offline)
            if hasattr(self, "_status_action"):
                self._status_action.setText(status_text)
            self.tray.setToolTip("\n".join(tip_lines))
            return

        age = time.time() - float(ts)
        age_str = self._fmt_age(age)
        status_text = f"Mommy was last evil {age_str} ago."

        # UTC display
        dt_utc = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        tip_lines.append(f"Last cmd (UTC): {dt_utc.strftime('%Y-%m-%d %H:%M:%SZ')}")
        tip_lines.append(f"Age: {age_str}")

        if age <= 600:
            self.tray.setIcon(self._icon_fresh)
        elif age <= 3600:
            self.tray.setIcon(self._icon_stale)
        else:
            self.tray.setIcon(self._icon_offline)

        if hasattr(self, "_status_action"):
            self._status_action.setText(status_text)
        self.tray.setToolTip("\n".join(tip_lines))


    def _rebuild_screens_menu(self) -> None:
        self._screens_menu.clear()

        # choices: [("All screens", None), ("Screen 0", [0]), ("Screen 1", [1]) ...]
        choices = self._get_screen_choices()
        current = self._get_selected_screens()  # list[int] | None

        for label, value in choices:
            act = QAction(label, self._screens_menu)
            act.setCheckable(True)
            act.setChecked(value == current)
            act.triggered.connect(lambda _=False, v=value: self._select_screens(v))
            self._screens_menu.addAction(act)

    def _select_screens(self, value: list[int] | None) -> None:
        self._set_selected_screens(value)
        self.screens_changed.emit(value)
        self._rebuild_screens_menu()

    def _rebuild_audio_menu(self) -> None:
        self._audio_menu.clear()

        choices = self._get_audio_choices()   # [("Default", None), ("Speakers (XYZ)", "idstring"), ...]
        current = self._get_selected_audio()

        for label, value in choices:
            act = QAction(label, self._audio_menu)
            act.setCheckable(True)
            act.setChecked(value == current)
            act.triggered.connect(lambda _=False, v=value: self._select_audio(v))
            self._audio_menu.addAction(act)

    def _select_audio(self, value: str | None) -> None:
        self._set_selected_audio(value)
        self.audio_device_changed.emit(value)
        self._rebuild_audio_menu()

