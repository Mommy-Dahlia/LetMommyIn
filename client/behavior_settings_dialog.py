from __future__ import annotations
from pathlib import Path
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QFormLayout, QCheckBox,
    QListWidget, QAbstractItemView, QScrollArea, QWidget
)
from behavior_manager import load_behaviors, save_behaviors, load_content_pool

class StepSpinBox(QWidget):
    valueChanged = Signal(float)

    def __init__(self, *, min_val: float, max_val: float, step: float = 1.0, 
                 decimals: int = 0, prefix: str = "", suffix: str = "", parent=None):
        super().__init__(parent)
        self._min = min_val
        self._max = max_val
        self._step = step
        self._decimals = decimals
        self._prefix = prefix
        self._suffix = suffix
        self._value = min_val

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self._btn_down = QPushButton("▼")
        self._btn_down.setFixedWidth(28)
        self._btn_down.clicked.connect(self._decrement)

        self._label = QLabel(self._fmt())
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setMinimumWidth(60)

        self._btn_up = QPushButton("▲")
        self._btn_up.setFixedWidth(28)
        self._btn_up.clicked.connect(self._increment)

        layout.addWidget(self._btn_down)
        layout.addWidget(self._label)
        layout.addWidget(self._btn_up)

    def _fmt(self) -> str:
        val = f"{self._value:.{self._decimals}f}"
        return f"{self._prefix}{val}{self._suffix}"

    def _decrement(self) -> None:
        self.setValue(self._value - self._step)

    def _increment(self) -> None:
        self.setValue(self._value + self._step)

    def value(self) -> float:
        return self._value

    def setValue(self, v: float) -> None:
        self._value = max(self._min, min(self._max, round(v, self._decimals)))
        self._label.setText(self._fmt())
        self.valueChanged.emit(self._value)

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

        self._start_h = StepSpinBox(min_val=0, max_val=23,
                                    suffix="h",
                                    parent=self)
        self._start_h.setValue(self._behaviors["active_time"]["start_h"])

        self._start_m = StepSpinBox(min_val=0, max_val=59,
                                    suffix="m",
                                    parent=self)
        self._start_m.setValue(self._behaviors["active_time"]["start_m"])
        
        self._end_h = StepSpinBox(min_val=0, max_val=23,
                                    suffix="h",
                                    parent=self)
        self._end_h.setValue(self._behaviors["active_time"]["end_h"])
        
        self._end_m = StepSpinBox(min_val=0, max_val=59,
                                    suffix="m",
                                    parent=self)
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

        self._min_minutes = StepSpinBox(min_val=0, max_val=1440,
                                    suffix="m",
                                    parent=self)
        self._min_minutes.setValue(self._behaviors["general_frequency"]["min_minutes"])

        self._random_minutes = StepSpinBox(min_val=0, max_val=1440,
                                    suffix="m",
                                    parent=self)
        self._random_minutes.setValue(self._behaviors["general_frequency"]["random_minutes"])
        
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
        self._max_usd = StepSpinBox(
            min_val=0.0,
            max_val=10000.0,
            step=0.50,
            decimals=2,
            prefix="$",
            suffix=" / day",
            parent=self
        )
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
        
        tags_group = QGroupBox("Allowed Content Tags")
        tags_layout = QVBoxLayout(tags_group)
        tags_layout.addWidget(QLabel(
            "Only behavior content with at least one allowed tag will fire."
        ))
        
        self._tag_checks: dict[str, QCheckBox] = {}
        allowed = set(self._behaviors.get("allowed_tags", []))
        
        known_tags: set[str] = set(self._behaviors.get("seen_tags", []))
        
        tag_scroll = QScrollArea()
        tag_scroll.setWidgetResizable(True)
        tag_inner = QWidget()
        tag_inner_layout = QVBoxLayout(tag_inner)
        
        if known_tags:
            for tag in sorted(known_tags):
                chk = QCheckBox(tag)
                chk.setChecked(tag in allowed)
                tag_inner_layout.addWidget(chk)
                self._tag_checks[tag] = chk
        else:
            tag_inner_layout.addWidget(QLabel("No behavior content received yet."))
        
        tag_scroll.setWidget(tag_inner)
        tag_scroll.setMinimumHeight(150)
        tags_layout.addWidget(tag_scroll)
        root.addWidget(tags_group)
        
        btn_row = QHBoxLayout()
        btn_save = QPushButton("Save")
        btn_cancel = QPushButton("Cancel")
        btn_save.clicked.connect(self._save)
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_cancel)
        outer.addLayout(btn_row)
        
    def _save(self) -> None:
        self._behaviors["active_time"]["start_h"] = int(self._start_h.value())
        self._behaviors["active_time"]["start_m"] = int(self._start_m.value())
        self._behaviors["active_time"]["end_h"] = int(self._end_h.value())
        self._behaviors["active_time"]["end_m"] = int(self._end_m.value())

        self._behaviors["general_frequency"]["min_minutes"] = int(self._min_minutes.value())
        self._behaviors["general_frequency"]["random_minutes"] = int(self._random_minutes.value())

        self._behaviors["enabled"]["toys_and_teases"] = self._chk_toys.isChecked()
        self._behaviors["enabled"]["rules_and_tasks"] = self._chk_rules.isChecked()
        self._behaviors["enabled"]["web_aided_tasks"] = self._chk_web.isChecked()
        self._behaviors["enabled"]["bunny_bomb"] = self._chk_bunny.isChecked()
        self._behaviors["enabled"]["autodrainer"] = self._chk_autodrainer.isChecked()
        self._behaviors["enabled"]["session"] = self._chk_session.isChecked()

        self._behaviors["bunny_bomb"]["audio_and_overlay"] = self._chk_bunny_audio.isChecked()
        self._behaviors["autodrainer"]["max_per_day_usd"] = self._max_usd.value()
        
        self._behaviors["allowed_tags"] = [
            tag for tag, chk in self._tag_checks.items()
            if chk.isChecked()
        ]

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