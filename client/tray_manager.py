from __future__ import annotations

import time
import os
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal, QTimer
from PySide6.QtGui import QIcon, QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QApplication, QInputDialog, QFileDialog

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
    session_selected = Signal(object)
    customizer_requested = Signal()
    pet_names_changed = Signal(object)          # payload: list[str] | None
    default_audio_url_changed = Signal(object)  # payload: str | None
    default_overlay_changed = Signal(object)    # payload: tuple[url|None, opacity:float, screen:int]
    toggle_session_pause = Signal()
    popup_sfx_changed = Signal(object)  # payload: str | None
    image_save_enabled_changed = Signal(object)  # payload: bool
    image_save_dir_changed = Signal(object)      # payload: str | None
    browse_sessions_requested = Signal()
    session_receive_mode_changed = Signal(object)  # payload: str

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
        get_session_choices: Callable[[], list[tuple[str, object]]],
        get_pet_names: Callable[[], list[str] | None],
        get_default_audio_url: Callable[[], str | None],
        get_default_overlay: Callable[[], tuple[str | None, float, int]],
    ):
        super().__init__()
        self.state = TrayState()
        self._icon_fresh = icon_fresh
        self._icon_stale = icon_stale
        self._icon_offline = icon_offline

        self._get_screen_choices = get_screen_choices
        self._get_audio_choices = get_audio_choices
        self._get_session_choices = get_session_choices
        self._get_pet_names = get_pet_names
        self._get_default_audio_url = get_default_audio_url
        self._get_default_overlay = get_default_overlay
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
        
        act_browse_sessions = QAction("Browse Sessions...", menu)
        act_browse_sessions.triggered.connect(self.browse_sessions_requested.emit)
        menu.addAction(act_browse_sessions)
        menu.addSeparator()

        # Screens submenu
        self._screens_menu = menu.addMenu("Popup screens")
        self._rebuild_screens_menu()

        # Audio submenu
        self._audio_menu = menu.addMenu("Audio output")
        self._rebuild_audio_menu()

        menu.addSeparator()
        
        self._defaults_menu = menu.addMenu("Defaults")

        act_pns = QAction("Set pet names (#PNS)...", self._defaults_menu)
        act_pns.triggered.connect(self._prompt_pet_names)
        self._defaults_menu.addAction(act_pns)

        act_audio = QAction("Set default audio URL...", self._defaults_menu)
        act_audio.triggered.connect(self._prompt_default_audio)
        self._defaults_menu.addAction(act_audio)

        act_overlay = QAction("Set default overlay URL...", self._defaults_menu)
        act_overlay.triggered.connect(self._prompt_default_overlay)
        self._defaults_menu.addAction(act_overlay)

        act_opacity = QAction("Set overlay opacity...", self._defaults_menu)
        act_opacity.triggered.connect(self._prompt_overlay_opacity)
        self._defaults_menu.addAction(act_opacity)

        act_screen = QAction("Set overlay screen (-1 = all)...", self._defaults_menu)
        act_screen.triggered.connect(self._prompt_overlay_screen)
        self._defaults_menu.addAction(act_screen)
        
        act_sfx = QAction("Set popup sound (local path)...", self._defaults_menu)
        act_sfx.triggered.connect(self._prompt_popup_sfx)
        self._defaults_menu.addAction(act_sfx)
        
        # --- Image saving defaults ---
        act_save_images = QAction("Save popped-up images", self._defaults_menu)
        act_save_images.setCheckable(True)
        act_save_images.setChecked(True)  # initial; client will sync actual state via setter below
        act_save_images.triggered.connect(lambda checked: self.image_save_enabled_changed.emit(bool(checked)))
        self._defaults_menu.addAction(act_save_images)

        act_save_folder = QAction("Set image save folder...", self._defaults_menu)
        act_save_folder.triggered.connect(self._prompt_image_save_dir)
        self._defaults_menu.addAction(act_save_folder)

        self._act_save_images = act_save_images  # keep a handle so client can sync UI state
        self._session_receive_menu = self._defaults_menu.addMenu("Incoming sessions")
        self._rebuild_session_receive_menu()
        
        menu.addSeparator()

        self._premium_menu = menu.addMenu("Premium")

        act_customizer = QAction("Session customizer...", self._premium_menu)
        act_customizer.setEnabled(False)  # locked until tier says paid
        act_customizer.triggered.connect(self.customizer_requested.emit)  # later
        self._premium_menu.addAction(act_customizer)

        self._act_customizer = act_customizer
        
        menu.addSeparator()
        
        act_pause = QAction("Pause/Resume session (Ctrl+Alt+F12)", menu)
        act_pause.triggered.connect(self.toggle_session_pause.emit)
        menu.addAction(act_pause)

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
            
    def _rebuild_session_receive_menu(self) -> None:
        self._session_receive_menu.clear()

        # We read current via ui_settings directly (keeps TrayManager signature stable)
        from ui_settings import get_session_receive_mode
        current = get_session_receive_mode()
    
        choices = [
            ("Full content warning (details + confirm)", "full"),
            ("Confirm only (no details)", "minimal"),
            ("Run immediately", "off"),
        ]

        for label, value in choices:
            act = QAction(label, self._session_receive_menu)
            act.setCheckable(True)
            act.setChecked(value == current)
            act.triggered.connect(lambda _=False, v=value: self._select_session_receive_mode(v))
            self._session_receive_menu.addAction(act)

    def _select_session_receive_mode(self, mode: str) -> None:
        self.session_receive_mode_changed.emit(mode)
        self._rebuild_session_receive_menu()

    def _select_audio(self, value: str | None) -> None:
        self._set_selected_audio(value)
        self.audio_device_changed.emit(value)
        self._rebuild_audio_menu()

    def _prompt_pet_names(self) -> None:
        current = self._get_pet_names() or []
        default_text = ", ".join(current)
        text, ok = QInputDialog.getText(
            None,
            "Pet Names (#PNS)",
            "Comma-separated pet names (blank = disable):",
            text=default_text,
        )
        if not ok:
            return
        raw = (text or "").strip()
        if not raw:
            self.pet_names_changed.emit(None)
            return
        names = [t.strip() for t in raw.split(",") if t.strip()]
        self.pet_names_changed.emit(names or None)

    def _prompt_default_audio(self) -> None:
        current = self._get_default_audio_url() or ""
        text, ok = QInputDialog.getText(
            None,
            "Default Audio URL",
            "Audio URL (blank = disable):",
            text=current,
        )
        if not ok:
            return
        url = (text or "").strip() or None
        self.default_audio_url_changed.emit(url)

    def _prompt_default_overlay(self) -> None:
        url, opacity, screen = self._get_default_overlay()
        current = url or ""
        text, ok = QInputDialog.getText(
            None,
            "Default Overlay URL",
            "Overlay GIF URL (blank = disable):",
            text=current,
        )
        if not ok:
            return
        new_url = (text or "").strip() or None
        self.default_overlay_changed.emit((new_url, float(opacity), int(screen)))
        
    def _prompt_overlay_opacity(self) -> None:
        url, opacity, screen = self._get_default_overlay()

        # getDouble(parent, title, label, value, minValue, maxValue, decimals)
        val, ok = QInputDialog.getDouble(
            None,
            "Overlay Opacity",
            "Opacity (0.0 - 1.0):",
            float(opacity),
            0.0,
            1.0,
            2,
        )
        if not ok:
            return
        self.default_overlay_changed.emit((url, float(val), int(screen)))


    def _prompt_overlay_screen(self) -> None:
        url, opacity, screen = self._get_default_overlay()

        # getInt(parent, title, label, value, minValue, maxValue, step)
        val, ok = QInputDialog.getInt(
            None,
            "Overlay Screen",
            "Screen index (-1 = all):",
            int(screen),
            -1,
            32,
            1,
        )
        if not ok:
            return
        self.default_overlay_changed.emit((url, float(opacity), int(val)))
        
    def _prompt_popup_sfx(self) -> None:
        # import lazily to keep tray_manager clean
        from ui_settings import get_popup_sfx_path
        current = get_popup_sfx_path() or ""
        text, ok = QInputDialog.getText(
            None,
            "Popup Sound Effect",
            "Local path to sound file (blank = disable):",
            text=current,
        )
        if not ok:
            return
        path = (text or "").strip() or None
        self.popup_sfx_changed.emit(path)

    def _prompt_image_save_dir(self) -> None:
        # Use a folder picker dialog (better than typing paths)
        folder = QFileDialog.getExistingDirectory(
            None,
            "Select image save folder",
            "",
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks,
        )
        folder = (folder or "").strip()
        if not folder:
            return
        self.image_save_dir_changed.emit(folder)

    def set_image_save_enabled_checked(self, enabled: bool) -> None:
        if hasattr(self, "_act_save_images"):
            self._act_save_images.setChecked(bool(enabled))

    def apply_feature_gates(self, tier: str) -> None:
        tier = (tier or "free").strip().lower()
        paid = (tier == "paid")

        if hasattr(self, "_act_customizer"):
            self._act_customizer.setEnabled(paid)
