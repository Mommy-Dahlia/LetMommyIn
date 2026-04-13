from __future__ import annotations
from pathlib import Path
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QFormLayout, QCheckBox, QDoubleSpinBox,
    QSpinBox, QListWidget, QAbstractItemView, QScrollArea, QWidget
)
from behavior_manager import load_behaviors, save_behaviors

class BehaviorSettingsDialog(QDialog):
    behaviors_changed = Signal(dict)

    def __init__(self, config_dir: Path, content_roots: list, compiler, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Automated Behaviors")
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self._config_dir = config_dir
        self._content_roots = content_roots
        self._compiler = compiler
        self._behaviors = load_behaviors(config_dir)

        # outer layout holds scroll area + buttons
        outer = QVBoxLayout(self)

        # scroll area so it doesn't go off screen on small displays
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        root = QVBoxLayout(inner)
        scroll.setWidget(inner)
        outer.addWidget(scroll)
        
        time_group = QGroupBox("Active Time")
        time_form = QFormLayout(time_group)

        self._start_h = QSpinBox()
        self._start_h.setRange(0, 23)
        self._start_h.setValue(self._behaviors["active_time"]["start_h"])

        self._start_m = QSpinBox()
        self._start_m.setRange(0, 59)
        self._start_m.setValue(self._behaviors["active_time"]["start_m"])

        self._end_h = QSpinBox()
        self._end_h.setRange(0, 23)
        self._end_h.setValue(self._behaviors["active_time"]["end_h"])

        self._end_m = QSpinBox()
        self._end_m.setRange(0, 59)
        self._end_m.setValue(self._behaviors["active_time"]["end_m"])

        start_row = QHBoxLayout()
        start_row.addWidget(QLabel("Hour:"))
        start_row.addWidget(self._start_h)
        start_row.addWidget(QLabel("Minute:"))
        start_row.addWidget(self._start_m)

        end_row = QHBoxLayout()
        end_row.addWidget(QLabel("Hour:"))
        end_row.addWidget(self._end_h)
        end_row.addWidget(QLabel("Minute:"))
        end_row.addWidget(self._end_m)

        time_form.addRow("Start:", start_row)
        time_form.addRow("End:", end_row)
        root.addWidget(time_group)
        
        freq_group = QGroupBox("General Frequency")
        freq_form = QFormLayout(freq_group)

        self._min_minutes = QSpinBox()
        self._min_minutes.setRange(1, 1440)
        self._min_minutes.setValue(
            int(self._behaviors["general_frequency"]["min_minutes"])
        )
        self._min_minutes.setSuffix(" min")

        self._random_minutes = QSpinBox()
        self._random_minutes.setRange(0, 1440)
        self._random_minutes.setValue(
            int(self._behaviors["general_frequency"]["random_minutes"])
        )
        self._random_minutes.setSuffix(" min")

        freq_form.addRow("Minimum between events:", self._min_minutes)
        freq_form.addRow("Additional random time:", self._random_minutes)
        root.addWidget(freq_group)
        
        enable_group = QGroupBox("Behaviors")
        enable_grid = QVBoxLayout(enable_group)
        enabled = self._behaviors["enabled"]

        # Toys and Teases - no extra settings
        toys_row = QHBoxLayout()
        self._chk_toys = QCheckBox("Toys and Teases")
        self._chk_toys.setChecked(bool(enabled.get("toys_and_teases")))
        toys_row.addWidget(self._chk_toys)
        toys_row.addStretch(1)
        enable_grid.addLayout(toys_row)

        # Rules and Tasks - no extra settings
        rules_row = QHBoxLayout()
        self._chk_rules = QCheckBox("Rules and Tasks")
        self._chk_rules.setChecked(bool(enabled.get("rules_and_tasks")))
        rules_row.addWidget(self._chk_rules)
        rules_row.addStretch(1)
        enable_grid.addLayout(rules_row)

        # Web-Aided Tasks - no extra settings
        web_row = QHBoxLayout()
        self._chk_web = QCheckBox("Web-Aided Tasks")
        self._chk_web.setChecked(bool(enabled.get("web_aided_tasks")))
        web_row.addWidget(self._chk_web)
        web_row.addStretch(1)
        enable_grid.addLayout(web_row)

        # BunnyBomb - audio/overlay toggle inline
        bunny_row = QHBoxLayout()
        self._chk_bunny = QCheckBox("BunnyBomb")
        self._chk_bunny.setChecked(bool(enabled.get("bunny_bomb")))
        self._chk_bunny_audio = QCheckBox("Audio and overlay")
        self._chk_bunny_audio.setChecked(
            bool(self._behaviors["bunny_bomb"]["audio_and_overlay"])
        )
        bunny_row.addWidget(self._chk_bunny)
        bunny_row.addSpacing(20)
        bunny_row.addWidget(self._chk_bunny_audio)
        bunny_row.addStretch(1)
        enable_grid.addLayout(bunny_row)

        # Autodrainer - max USD inline
        drain_row = QHBoxLayout()
        self._chk_autodrainer = QCheckBox("Autodrainer")
        self._chk_autodrainer.setChecked(bool(enabled.get("autodrainer")))
        self._max_usd = QDoubleSpinBox()
        self._max_usd.setRange(0.0, 10000.0)
        self._max_usd.setDecimals(2)
        self._max_usd.setPrefix("$")
        self._max_usd.setSuffix(" / day")
        self._max_usd.setValue(
            float(self._behaviors["autodrainer"]["max_per_day_usd"])
        )
        drain_row.addWidget(self._chk_autodrainer)
        drain_row.addSpacing(20)
        drain_row.addWidget(QLabel("Max:"))
        drain_row.addWidget(self._max_usd)
        drain_row.addStretch(1)
        enable_grid.addLayout(drain_row)

        # Auto-Session - button to sessions menu inline
        session_row = QHBoxLayout()
        self._chk_session = QCheckBox("Auto-Session")
        self._chk_session.setChecked(bool(enabled.get("session")))
        btn_sessions_menu = QPushButton("Configure allowed sessions...")
        btn_sessions_menu.clicked.connect(self._open_sessions_menu)
        session_row.addWidget(self._chk_session)
        session_row.addSpacing(20)
        session_row.addWidget(btn_sessions_menu)
        session_row.addStretch(1)
        enable_grid.addLayout(session_row)

        root.addWidget(enable_group)
        
        btn_row = QHBoxLayout()
        btn_save = QPushButton("Save")
        btn_cancel = QPushButton("Cancel")
        btn_save.clicked.connect(self._save)
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_cancel)
        outer.addLayout(btn_row)
        
    def _save(self) -> None:
        self._behaviors["active_time"]["start_h"] = self._start_h.value()
        self._behaviors["active_time"]["start_m"] = self._start_m.value()
        self._behaviors["active_time"]["end_h"] = self._end_h.value()
        self._behaviors["active_time"]["end_m"] = self._end_m.value()

        self._behaviors["general_frequency"]["min_minutes"] = self._min_minutes.value()
        self._behaviors["general_frequency"]["random_minutes"] = self._random_minutes.value()

        self._behaviors["enabled"]["toys_and_teases"] = self._chk_toys.isChecked()
        self._behaviors["enabled"]["rules_and_tasks"] = self._chk_rules.isChecked()
        self._behaviors["enabled"]["web_aided_tasks"] = self._chk_web.isChecked()
        self._behaviors["enabled"]["bunny_bomb"] = self._chk_bunny.isChecked()
        self._behaviors["enabled"]["autodrainer"] = self._chk_autodrainer.isChecked()
        self._behaviors["enabled"]["session"] = self._chk_session.isChecked()

        self._behaviors["bunny_bomb"]["audio_and_overlay"] = self._chk_bunny_audio.isChecked()
        self._behaviors["autodrainer"]["max_per_day_usd"] = self._max_usd.value()

        save_behaviors(self._config_dir, self._behaviors)
        self.behaviors_changed.emit(self._behaviors)
        self.accept()
    
    def _open_sessions_menu(self) -> None:
        from session_launcher import SessionLauncherDialog

        def _on_allowed_changed(new_allowed: list[str]) -> None:
            self._behaviors["session"]["allowed_sessions"] = new_allowed
            save_behaviors(self._config_dir, self._behaviors)

        dlg = SessionLauncherDialog(
            content_roots=self._content_roots,
            compiler=self._compiler,
            allowed_sessions=self._behaviors["session"]["allowed_sessions"],
            on_allowed_changed=_on_allowed_changed,
            parent=self,
        )
        dlg.exec()