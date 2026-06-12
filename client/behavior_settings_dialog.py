from __future__ import annotations
from pathlib import Path
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QFormLayout, QCheckBox,
    QListWidget, QAbstractItemView, QScrollArea, QWidget,
    QComboBox, QLineEdit, QGridLayout
)
from behavior_manager import load_behaviors, save_behaviors, AUTODRAINER_URLS

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

    def __init__(self, config_dir: Path, content_roots: list, compiler, tier: str = "free", parent=None):
        super().__init__(parent)
        self._tier = tier
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
        
        wfm_row = QHBoxLayout()
        self._chk_wfm = QCheckBox("Write For Me")
        self._chk_wfm.setChecked(bool(enabled.get("wfm")))
        wfm_row.addWidget(self._chk_wfm)
        wfm_row.addStretch(1)
        enable_grid.addLayout(wfm_row)
        
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
        
        either_or_row = QHBoxLayout()
        self._chk_either_or = QCheckBox("Either/Or Tasks")
        self._chk_either_or.setChecked(bool(enabled.get("either_or")))
        either_or_row.addWidget(self._chk_either_or)
        either_or_row.addStretch(1)
        enable_grid.addLayout(either_or_row)
        
        wallpaper_row = QHBoxLayout()
        self._chk_wallpaper = QCheckBox("Wallpaper Changes")
        self._chk_wallpaper.setChecked(bool(enabled.get("wallpaper")))
        wallpaper_row.addWidget(self._chk_wallpaper)
        wallpaper_row.addStretch(1)
        enable_grid.addLayout(wallpaper_row)

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
        self._max_item_combo = QComboBox()
        self._max_item_combo.addItem("No limit", 0.0)
        for price in sorted(set(cost for _, cost in AUTODRAINER_URLS)):
            self._max_item_combo.addItem(f"${price:.2f}", price)
            
        current_max = float(self._behaviors["autodrainer"].get("max_item_usd", 0.0))
        idx = self._max_item_combo.findData(current_max)
        if idx >= 0:
            self._max_item_combo.setCurrentIndex(idx)
        drain_row.addWidget(self._chk_autodrainer)
        drain_row.addSpacing(20)
        drain_row.addWidget(QLabel("Max:"))
        drain_row.addWidget(self._max_usd)
        drain_row.addSpacing(10)
        drain_row.addWidget(QLabel("Item max:"))
        drain_row.addWidget(self._max_item_combo)
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
        
        if self._tier != "paid":
            for chk in (self._chk_rules, self._chk_web, self._chk_either_or,
                        self._chk_autodrainer, self._chk_session, self._chk_wallpaper):
                chk.setEnabled(False)
                chk.setStyleSheet("QCheckBox { color: #999999; }")
            self._max_usd.setEnabled(False)

            premium_label = QLabel("Additional behaviors available with premium.")
            premium_label.setStyleSheet("QLabel { color: #999999; font-style: italic; }")
            enable_grid.addWidget(premium_label)

        root.addWidget(enable_group)
        
        tags_group = QGroupBox("Allowed Content Tags")
        tags_layout = QVBoxLayout(tags_group)
        tags_layout.addWidget(QLabel(
            "Only behavior content with all tags allowed."
        ))
        
        self._tag_checks: dict[str, QCheckBox] = {}
        allowed = set(self._behaviors.get("allowed_tags", []))
        
        known_tags: set[str] = set(self._behaviors.get("seen_tags", []))
        
        tag_scroll = QScrollArea()
        tag_scroll.setWidgetResizable(True)
        tag_inner = QWidget()
        tag_inner_layout = QVBoxLayout(tag_inner)
        
        if known_tags:
            tag_grid = QGridLayout()
            for i, tag in enumerate(sorted(known_tags)):
                chk = QCheckBox(tag)
                chk.setChecked(tag in allowed)
                tag_grid.addWidget(chk, i // 3, i % 3)
                self._tag_checks[tag] = chk
            tag_inner_layout.addLayout(tag_grid)
        else:
            tag_inner_layout.addWidget(QLabel("No behavior content received yet."))        
            
        tag_scroll.setWidget(tag_inner)
        tag_scroll.setMinimumHeight(150)
        tags_layout.addWidget(tag_scroll)
        root.addWidget(tags_group)
        
        if self._tier == "paid":
            profile_group = QGroupBox("Profiles & Weights")
            profile_layout = QVBoxLayout(profile_group)
            
            profile_row = QHBoxLayout()
            profile_row.addWidget(QLabel("Active profile:"))
            
            self._profile_combo = self._build_profile_combo()
            profile_row.addWidget(self._profile_combo)
            
            btn_new_profile = QPushButton("New...")
            btn_new_profile.clicked.connect(self._create_profile)
            profile_row.addWidget(btn_new_profile)
            
            btn_del_profile = QPushButton("Delete")
            btn_del_profile.clicked.connect(self._delete_profile)
            profile_row.addWidget(btn_del_profile)
            
            profile_layout.addLayout(profile_row)
            
            profile_layout.addWidget(QLabel("Behavior weights (1.0 = normal):"))
            
            self._weight_area = QVBoxLayout()
            profile_layout.addLayout(self._weight_area)
            
            profile_layout.addWidget(QLabel("Tag weights (1.0 = normal):"))
            
            self._tag_weight_area = QVBoxLayout()
            profile_layout.addLayout(self._tag_weight_area)
            
            root.addWidget(profile_group)
            
            self._load_profile_into_ui()
            self._rebuild_weights_ui()
            
            schedule_group = QGroupBox("Schedule")
            schedule_layout = QVBoxLayout(schedule_group)
            
            self._chk_schedule = QCheckBox("Enable schedule")
            self._chk_schedule.setChecked(bool(self._behaviors.get("schedule_enabled")))
            schedule_layout.addWidget(self._chk_schedule)
            
            schedule_layout.addWidget(QLabel("Each slot activates a profile at the specified time."))
            
            self._schedule_rows = []
            self._schedule_list = QVBoxLayout()
            schedule_layout.addLayout(self._schedule_list)
            
            for slot in self._behaviors.get("schedule", []):
                self._insert_schedule_row(
                    int(slot.get("start_h", 0)),
                    int(slot.get("start_m", 0)),
                    slot.get("profile"),
                )
            
            add_row = QHBoxLayout()
            btn_add_slot = QPushButton("Add time slot")
            btn_add_slot.clicked.connect(self._add_schedule_slot)
            add_row.addWidget(btn_add_slot)
            add_row.addStretch(1)
            schedule_layout.addLayout(add_row)
            
            root.addWidget(schedule_group)
                
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
        
        self._behaviors["bunny_bomb"]["audio_and_overlay"] = self._chk_bunny_audio.isChecked()
        self._behaviors["autodrainer"]["max_per_day_usd"] = self._max_usd.value()
        self._behaviors["autodrainer"]["max_item_usd"] = self._max_item_combo.currentData()
        self._behaviors["enabled"]["autodrainer"] = self._chk_autodrainer.isChecked()
        
        self._behaviors["allowed_tags"] = [
            tag for tag, chk in self._tag_checks.items()
            if chk.isChecked()
        ]
        
        enabled = {
            "toys_and_teases": self._chk_toys.isChecked(),
            "rules_and_tasks": self._chk_rules.isChecked(),
            "web_aided_tasks": self._chk_web.isChecked(),
            "wfm": self._chk_wfm.isChecked(),
            "either_or": self._chk_either_or.isChecked(),
            "wallpaper": self._chk_wallpaper.isChecked(),
            "bunny_bomb": self._chk_bunny.isChecked(),
            "session": self._chk_session.isChecked(),
        }
        
        freq = {
            "min_minutes": int(self._min_minutes.value()),
            "random_minutes": int(self._random_minutes.value()),
        }
        
        if self._tier == "paid" and hasattr(self, "_profile_combo"):
            profile_name = self._profile_combo.currentData()
            
            bw = {}
            for name, row_info in self._bw_rows.items():
                try:
                    val = float(row_info["field"].text())
                except ValueError:
                    val = 1.0
                if val != 1.0:
                    bw[name] = val

            tw = {}
            for tag, row_info in self._tw_rows.items():
                try:
                    val = float(row_info["field"].text())
                except ValueError:
                    val = 1.0
                if val != 1.0:
                    tw[tag] = val
            
            if profile_name:
                profile = self._behaviors.setdefault("profiles", {}).setdefault(profile_name, {})
                profile["enabled"] = enabled
                profile["general_frequency"] = freq
                profile["behavior_weights"] = bw
                profile["tag_weights"] = tw
            else:
                self._behaviors["enabled"] = enabled
                self._behaviors["general_frequency"] = freq
                self._behaviors["behavior_weights"] = bw
                self._behaviors["tag_weights"] = tw
            
            self._behaviors["active_profile"] = profile_name
            
            self._behaviors["schedule_enabled"] = self._chk_schedule.isChecked()

            schedule = []
            for row_info in self._schedule_rows:
                schedule.append({
                    "start_h": int(row_info["hour"].value()),
                    "start_m": int(row_info["min"].value()),
                    "profile": row_info["combo"].currentData(),
                })
            schedule.sort(key=lambda s: s["start_h"] * 60 + s["start_m"])
            self._behaviors["schedule"] = schedule
        else:
            self._behaviors["enabled"] = enabled
            self._behaviors["general_frequency"] = freq
            
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
        
    def _build_profile_combo(self):
        from PySide6.QtWidgets import QComboBox
        combo = QComboBox()
        combo.addItem("Base settings", None)
        for name in sorted(self._behaviors.get("profiles", {}).keys()):
            combo.addItem(name, name)
        
        active = self._behaviors.get("active_profile")
        if active:
            idx = combo.findData(active)
            if idx >= 0:
                combo.setCurrentIndex(idx)
        
        combo.currentIndexChanged.connect(self._on_profile_changed)
        return combo
    
    def _create_profile(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "New Profile", "Profile name:")
        name = (name or "").strip()
        if not ok or not name:
            return
        if name in self._behaviors.get("profiles", {}):
            return
        
        profiles = self._behaviors.setdefault("profiles", {})
        profiles[name] = {
            "enabled": dict(self._behaviors.get("enabled", {})),
            "behavior_weights": {},
            "tag_weights": {},
            "general_frequency": dict(self._behaviors.get("general_frequency", {})),
        }
        
        self._profile_combo.addItem(name, name)
        self._profile_combo.setCurrentIndex(self._profile_combo.count() - 1)
    
    def _delete_profile(self) -> None:
        name = self._profile_combo.currentData()
        if name is None:
            return
        profiles = self._behaviors.get("profiles", {})
        profiles.pop(name, None)
        if self._behaviors.get("active_profile") == name:
            self._behaviors["active_profile"] = None
        idx = self._profile_combo.currentIndex()
        self._profile_combo.setCurrentIndex(0)
        self._profile_combo.removeItem(idx)
        
    def _on_profile_changed(self, index: int) -> None:
        self._load_profile_into_ui()
        self._rebuild_weights_ui()
    
    def _load_profile_into_ui(self) -> None:
        profile_name = self._profile_combo.currentData()
        if profile_name:
            source = self._behaviors.get("profiles", {}).get(profile_name, {})
            enabled = source.get("enabled", self._behaviors.get("enabled", {}))
            freq = source.get("general_frequency", self._behaviors.get("general_frequency", {}))
        else:
            enabled = self._behaviors.get("enabled", {})
            freq = self._behaviors.get("general_frequency", {})

        self._chk_toys.setChecked(bool(enabled.get("toys_and_teases")))
        self._chk_rules.setChecked(bool(enabled.get("rules_and_tasks")))
        self._chk_web.setChecked(bool(enabled.get("web_aided_tasks")))
        self._chk_wfm.setChecked(bool(enabled.get("wfm")))
        self._chk_either_or.setChecked(bool(enabled.get("either_or")))
        self._chk_wallpaper.setChecked(bool(enabled.get("wallpaper")))
        self._chk_bunny.setChecked(bool(enabled.get("bunny_bomb")))
        self._chk_autodrainer.setChecked(bool(self._behaviors.get("enabled", {}).get("autodrainer")))
        self._chk_session.setChecked(bool(enabled.get("session")))
        
        self._min_minutes.setValue(int(freq.get("min_minutes", 30)))
        self._random_minutes.setValue(int(freq.get("random_minutes", 15)))    
    
    def _rebuild_weights_ui(self) -> None:
        self._clear_layout(self._weight_area)
        self._clear_layout(self._tag_weight_area)
        
        profile_name = self._profile_combo.currentData()
        if profile_name:
            source = self._behaviors.get("profiles", {}).get(profile_name, {})
        else:
            source = self._behaviors
        
        # ---- Behavior weights ----
        bw = {k: v for k, v in source.get("behavior_weights", {}).items() if v != 1.0}
        self._bw_rows = {}
        
        bw_add_row = QHBoxLayout()
        self._bw_combo = QComboBox()
        self._bw_combo.addItem("Add behavior weight...", None)
        for name in ("toys_and_teases", "rules_and_tasks", "web_aided_tasks",
                     "wfm", "either_or", "bunny_bomb", "session", "wallpaper"):
            if name not in bw:
                self._bw_combo.addItem(name.replace("_", " ").title(), name)
        bw_add_row.addWidget(self._bw_combo)
        self._bw_combo.currentIndexChanged.connect(self._add_behavior_weight_row)
        self._weight_area.addLayout(bw_add_row)
        
        for name, val in sorted(bw.items()):
            self._insert_behavior_weight_row(name, val)
        
        # ---- Tag weights ----
        tw = {k: v for k, v in source.get("tag_weights", {}).items() if v != 1.0}
        self._tw_rows = {}
        
        known_tags = sorted(set(self._behaviors.get("seen_tags", [])))
        
        tw_add_row = QHBoxLayout()
        self._tw_combo = QComboBox()
        self._tw_combo.addItem("Add tag weight...", None)
        for tag in known_tags:
            if tag not in tw:
                self._tw_combo.addItem(tag, tag)
        tw_add_row.addWidget(self._tw_combo)
        self._tw_combo.currentIndexChanged.connect(self._add_tag_weight_row)
        self._tag_weight_area.addLayout(tw_add_row)

        for tag, val in sorted(tw.items()):
            self._insert_tag_weight_row(tag, val)
            
    def _insert_behavior_weight_row(self, name: str, val: float) -> None:
        row = QHBoxLayout()
        label = QLabel(name.replace("_", " ").title())
        label.setMinimumWidth(140)
        
        field = QLineEdit(str(val))
        field.setFixedWidth(60)
        
        btn_remove = QPushButton("×")
        btn_remove.setFixedWidth(24)
        btn_remove.clicked.connect(lambda: self._remove_behavior_weight(name))
        
        row.addWidget(label)
        row.addWidget(field)
        row.addWidget(btn_remove)
        row.addStretch(1)

        self._weight_area.addLayout(row)
        self._bw_rows[name] = {"layout": row, "field": field}
        
    def _insert_tag_weight_row(self, tag: str, val: float) -> None:
        row = QHBoxLayout()
        label = QLabel(tag)
        label.setMinimumWidth(140)
        
        field = QLineEdit(str(val))
        field.setFixedWidth(60)
        
        btn_remove = QPushButton("×")
        btn_remove.setFixedWidth(24)
        btn_remove.clicked.connect(lambda: self._remove_tag_weight(tag))
        
        row.addWidget(label)
        row.addWidget(field)
        row.addWidget(btn_remove)
        row.addStretch(1)
        
        self._tag_weight_area.addLayout(row)
        self._tw_rows[tag] = {"layout": row, "field": field}
        
    def _add_behavior_weight_row(self, index: int) -> None:
       name = self._bw_combo.currentData()
       if name is None:
           return
       self._bw_combo.setCurrentIndex(0)
       self._bw_combo.removeItem(index)
       self._insert_behavior_weight_row(name, 1.0)
       
    def _add_tag_weight_row(self, index: int) -> None:
        tag = self._tw_combo.currentData()
        if tag is None:
            return
        self._tw_combo.setCurrentIndex(0)
        self._tw_combo.removeItem(index)
        self._insert_tag_weight_row(tag, 1.0)
            
    def _remove_behavior_weight(self, name: str) -> None:
        if name not in self._bw_rows:
            return
        row_info = self._bw_rows.pop(name)
        self._clear_layout(row_info["layout"])
        self._bw_combo.addItem(name.replace("_", " ").title(), name)
    
    def _remove_tag_weight(self, tag: str) -> None:
        if tag not in self._tw_rows:
            return
        row_info = self._tw_rows.pop(tag)
        self._clear_layout(row_info["layout"])
        self._tw_combo.addItem(tag, tag)
    
    def _insert_schedule_row(self, h: int, m: int, profile_name: str | None) -> None:
        row = QHBoxLayout()
            
        hour_spin = StepSpinBox(min_val=0, max_val=23, suffix="h", parent=self)
        hour_spin.setValue(h)
        
        min_spin = StepSpinBox(min_val=0, max_val=59, suffix="m", parent=self)
        min_spin.setValue(m)
        
        combo = QComboBox()
        combo.addItem("Base settings", None)
        for name in sorted(self._behaviors.get("profiles", {}).keys()):
            combo.addItem(name, name)
        if profile_name:
            idx = combo.findData(profile_name)
            if idx >= 0:
                combo.setCurrentIndex(idx)

        btn_remove = QPushButton("×")
        btn_remove.setFixedWidth(24)
        
        row.addWidget(hour_spin)
        row.addWidget(min_spin)
        row.addWidget(QLabel("→"))
        row.addWidget(combo)
        row.addWidget(btn_remove)
        row.addStretch(1)
        
        row_info = {"layout": row, "hour": hour_spin, "min": min_spin, "combo": combo}
        self._schedule_rows.append(row_info)
        self._schedule_list.addLayout(row)
        
        btn_remove.clicked.connect(lambda: self._remove_schedule_row(row_info))
    
    def _add_schedule_slot(self) -> None:
        self._insert_schedule_row(8, 0, None)

    def _remove_schedule_row(self, row_info: dict) -> None:
        if row_info in self._schedule_rows:
            self._schedule_rows.remove(row_info)
        self._clear_layout(row_info["layout"])

    def _clear_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())        