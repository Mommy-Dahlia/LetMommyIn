from __future__ import annotations
import sys
from pathlib import Path
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QComboBox, QCheckBox,
    QApplication, QGroupBox, QFormLayout, QGridLayout
)
from PySide6.QtGui import QFont
from behavior_manager import load_behaviors, save_behaviors, FREE_BEHAVIORS, AUTODRAINER_URLS
from behavior_settings_dialog import StepSpinBox
from presets import (
    build_mommy_profile, build_work_profile, build_work_schedule,
    REQUIRED_CHOICE_TAGS
)
from ui_settings import (
    set_pet_names, set_session_receive_mode, set_image_popup_opacity,
    set_image_click_through, set_popup_screens, set_image_save_enabled,
    set_wallpaper_set_cmd, set_wallpaper_get_cmd
)

REQUIRED_TAG_CHOICES = [
    {
        "label": "Gendered content — pick what fits you~",
        "tags": sorted(REQUIRED_CHOICE_TAGS),
    },
]


def _step_pet_names(cfg, save_config) -> bool:
    dlg = QDialog(None)
    dlg.setWindowTitle("Welcome~")
    dlg.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    layout = QVBoxLayout(dlg)

    layout.addWidget(QLabel("What pet names should Mommy use with you, darling~?"))
    layout.addWidget(QLabel("Comma-separated pet names:"))

    field = QLineEdit()
    current = cfg.pet_names or []
    field.setText(", ".join(current))
    field.setPlaceholderText("pet, toy, darling, sweetheart")
    layout.addWidget(field)

    btn = QPushButton("Next")
    btn.clicked.connect(dlg.accept)
    layout.addWidget(btn)

    if dlg.exec() != QDialog.Accepted:
        return False

    raw = (field.text() or "").strip()
    names = [t.strip() for t in raw.split(",") if t.strip()] or None
    cfg.pet_names = names
    save_config(cfg)
    set_pet_names(names)
    return True

def _step_tier_check(cfg, save_config, tier_signal) -> str:
    """Returns 'free' or 'paid'."""
    
    # First, ask what they expect
    dlg = QDialog(None)
    dlg.setWindowTitle("Account Type")
    dlg.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    layout = QVBoxLayout(dlg)

    layout.addWidget(QLabel("Are you joining as a free or paid user~?"))

    btn_row = QHBoxLayout()
    btn_free = QPushButton("Free")
    btn_paid = QPushButton("Paid")

    choice = {"value": "free"}

    def pick_free():
        choice["value"] = "free"
        dlg.accept()

    def pick_paid():
        choice["value"] = "paid"
        dlg.accept()

    btn_free.clicked.connect(pick_free)
    btn_paid.clicked.connect(pick_paid)
    btn_row.addWidget(btn_free)
    btn_row.addWidget(btn_paid)
    layout.addLayout(btn_row)

    dlg.exec()

    if choice["value"] == "free":
        return "free"

    # They said paid — check if tier is already confirmed
    if getattr(cfg, "tier", "free") == "paid":
        return "paid"

    # Not yet confirmed — show the waiting dialog
    return _step_wait_for_paid(cfg, save_config, tier_signal)

def _step_wait_for_paid(cfg, save_config, tier_signal) -> str:
    dlg = QDialog(None)
    dlg.setWindowTitle("Waiting for confirmation~")
    dlg.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    layout = QVBoxLayout(dlg)

    layout.addWidget(QLabel(
        "Mommy hasn't set up your paid access yet~\n"
        "Reach out to Mommy to get this sorted, darling.\n"
        "This will continue automatically once confirmed~\n"
        "Alternatively, you can continue to set up as a free user while you wait~"
    ))

    status_label = QLabel("Waiting...")
    status_label.setStyleSheet("QLabel { font-style: italic; color: #999; }")
    layout.addWidget(status_label)

    btn_free = QPushButton("Continue as free")
    layout.addWidget(btn_free)

    result = {"tier": "free"}

    def on_continue_free():
        result["tier"] = "free"
        dlg.accept()

    btn_free.clicked.connect(on_continue_free)

    # Listen for tier changes from the heartbeat
    if tier_signal is not None:
        def on_tier_changed(new_tier):
            if str(new_tier).strip().lower() == "paid":
                result["tier"] = "paid"
                dlg.accept()

        tier_signal.connect(on_tier_changed)

    dlg.exec()

    # Disconnect if we connected
    if tier_signal is not None:
        try:
            tier_signal.disconnect()
        except Exception:
            pass

    return result["tier"]

def _step_mommy_settings(cfg, config_dir, save_config, behavior_manager, tier) -> bool:
    dlg = QDialog(None)
    dlg.setWindowTitle("Mommy's Settings~")
    dlg.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    layout = QVBoxLayout(dlg)

    layout.addWidget(QLabel(
        "Would you like Mommy to set things up for you~?\n"
        "If you're not too worried about the settings, \n"
        "or are the type of subby slut who just wants things chosen for you, \n"
        "this will create a profile tuned to Mommy's preferences~"
    ))

    btn_row = QHBoxLayout()
    btn_yes = QPushButton("Yes please, Mommy~")
    btn_skip = QPushButton("I'll configure myself")

    choice = {"used": False}

    def pick_mommy():
        choice["used"] = True
        dlg.accept()

    def pick_manual():
        choice["used"] = False
        dlg.accept()

    btn_yes.clicked.connect(pick_mommy)
    btn_skip.clicked.connect(pick_manual)
    btn_row.addWidget(btn_yes)
    btn_row.addWidget(btn_skip)
    layout.addLayout(btn_row)

    dlg.exec()

    if not choice["used"]:
        return False

    # Apply Mommy's settings — fill in your values here
    behaviors = load_behaviors(config_dir)
    behaviors["profiles"]["Mommy"] = build_mommy_profile(tier=tier)
    behaviors["active_profile"] = "Mommy"
    behaviors["allowed_tags"] = [
        t for t in behaviors.get("seen_tags", [])
        if t not in REQUIRED_CHOICE_TAGS
    ]   
    save_behaviors(config_dir, behaviors)
    behavior_manager.update_behaviors(behaviors)

    # Apply operation settings — fill in your values
    from ui_settings import set_session_receive_mode, set_image_popup_opacity, set_image_click_through
    cfg.session_receive_mode = "minimal"
    cfg.image_popup_opacity = 1.0
    cfg.image_click_through = False
    save_config(cfg)
    set_session_receive_mode("minimal")
    set_image_popup_opacity(1.0)
    set_image_click_through(False)

    return True

def _step_required_tags(cfg, config_dir, save_config, behavior_manager):
    from behavior_manager import load_behaviors, save_behaviors

    behaviors = load_behaviors(config_dir)
    allowed = set(behaviors.get("allowed_tags", []))

    dlg = QDialog(None)
    dlg.setWindowTitle("Content Preferences")
    dlg.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    layout = QVBoxLayout(dlg)

    all_checks = {}

    for group in REQUIRED_TAG_CHOICES:
        layout.addWidget(QLabel(group["label"]))
        for tag in group["tags"]:
            chk = QCheckBox(tag)
            chk.setChecked(tag in allowed)
            layout.addWidget(chk)
            all_checks[tag] = chk

    btn = QPushButton("Next")
    btn.clicked.connect(dlg.accept)
    layout.addWidget(btn)

    if dlg.exec() != QDialog.Accepted:
        return

    for tag, chk in all_checks.items():
        if chk.isChecked():
            allowed.add(tag)
        else:
            allowed.discard(tag)

    behaviors["allowed_tags"] = list(allowed)
    save_behaviors(config_dir, behaviors)
    behavior_manager.update_behaviors(behaviors)
    
def _wait_for_tags(config_dir, timeout_ms=5000):
    from behavior_manager import load_behaviors
    
    behaviors = load_behaviors(config_dir)
    if behaviors.get("seen_tags"):
        return
    
    dlg = QDialog(None)
    dlg.setWindowTitle("Getting ready~")
    dlg.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    layout = QVBoxLayout(dlg)
    layout.addWidget(QLabel("Mommy's loading your content, one moment~"))

    QTimer.singleShot(timeout_ms, dlg.accept)

    check_timer = QTimer()
    def _check():
        b = load_behaviors(config_dir)
        if b.get("seen_tags"):
            dlg.accept()
    check_timer.timeout.connect(_check)
    check_timer.start(500)

    dlg.exec()
    check_timer.stop()
    
def _step_behavior_basics(cfg, config_dir, save_config, behavior_manager, profile_name: str ="Onboarding"):
    """Manual behavior setup — enable/disable, tags, frequency."""

    behaviors = load_behaviors(config_dir)
    tier = getattr(cfg, "tier", "free")

    dlg = QDialog(None)
    dlg.setWindowTitle("Behavior Setup")
    dlg.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    layout = QVBoxLayout(dlg)

    layout.addWidget(QLabel("Which automated behaviors would you like~?"))

    # Build checkboxes for each behavior
    checks = {}
    enabled = behaviors.get("enabled", {})

    all_behaviors = [
        ("toys_and_teases", "Toys and Teases"),
        ("wfm", "Write For Me"),
        ("bunny_bomb", "BunnyBomb"),
        ("rules_and_tasks", "Rules and Tasks"),
        ("either_or", "Either/Or Tasks"),
        ("web_aided_tasks", "Web-Aided Tasks"),
        ("session", "Auto-Session"),
        ("wallpaper", "Wallpaper Changes"),
    ]

    for key, label in all_behaviors:
        chk = QCheckBox(label)
        chk.setChecked(bool(enabled.get(key)))
        if tier != "paid" and key not in FREE_BEHAVIORS:
            chk.setEnabled(False)
            chk.setStyleSheet("QCheckBox { color: #999999; }")
        layout.addWidget(chk)
        checks[key] = chk

    # Tag checkboxes
    known_tags = sorted(set(behaviors.get("seen_tags", [])))
    tag_checks = {}
    if known_tags:
        layout.addWidget(QLabel("Which content tags to allow:"))
        allowed = set(behaviors.get("allowed_tags", []))
        tag_grid = QGridLayout()
        for i, tag in enumerate(sorted(known_tags)):
            chk = QCheckBox(tag)
            chk.setChecked(tag in allowed)
            tag_grid.addWidget(chk, i // 3, i % 3)
            tag_checks[tag] = chk
        layout.addLayout(tag_grid)

    # Frequency
    layout.addWidget(QLabel("How often should behaviors fire?"))

    freq = behaviors.get("general_frequency", {})
    freq_row = QHBoxLayout()
    freq_row.addWidget(QLabel("Minimum:"))
    min_spin = StepSpinBox(min_val=0, max_val=1440, suffix="m", parent=dlg)
    min_spin.setValue(int(freq.get("min_minutes", 30)))
    freq_row.addWidget(min_spin)
    freq_row.addWidget(QLabel("Random extra:"))
    rnd_spin = StepSpinBox(min_val=0, max_val=1440, suffix="m", parent=dlg)
    rnd_spin.setValue(int(freq.get("random_minutes", 15)))
    freq_row.addWidget(rnd_spin)
    layout.addLayout(freq_row)

    btn = QPushButton("Next")
    btn.clicked.connect(dlg.accept)
    layout.addWidget(btn)

    if dlg.exec() != QDialog.Accepted:
        return

    enabled = {}
    for key, chk in checks.items():
        enabled[key] = chk.isChecked()

    freq = {
        "min_minutes": int(min_spin.value()),
        "random_minutes": int(rnd_spin.value()),
    }

    behaviors = load_behaviors(config_dir)

    behaviors["profiles"][profile_name] = {
        "enabled": enabled,
        "general_frequency": freq,
        "behavior_weights": {},
        "tag_weights": {},
    }
    behaviors["active_profile"] = profile_name
    behaviors["allowed_tags"] = [t for t, c in tag_checks.items() if c.isChecked()]

    save_behaviors(config_dir, behaviors)
    behavior_manager.update_behaviors(behaviors)
    
def _step_autodrainer(cfg, config_dir, save_config, behavior_manager):
    """Paid-only autodrainer setup."""

    behaviors = load_behaviors(config_dir)

    dlg = QDialog(None)
    dlg.setWindowTitle("Autodrainer~")
    dlg.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    layout = QVBoxLayout(dlg)

    # Your pitch text goes here
    layout.addWidget(QLabel("Placeholder — your autodrainer description here"))

    chk_enable = QCheckBox("Enable Autodrainer")
    chk_enable.setChecked(bool(behaviors.get("enabled", {}).get("autodrainer")))
    layout.addWidget(chk_enable)

    layout.addWidget(QLabel("Daily budget:"))
    budget_spin = StepSpinBox(
        min_val=0.0, max_val=10000.0, step=0.50,
        decimals=2, prefix="$", suffix=" / day", parent=dlg
    )
    budget_spin.setValue(float(behaviors.get("autodrainer", {}).get("max_per_day_usd", 0.0)))
    layout.addWidget(budget_spin)

    layout.addWidget(QLabel("Maximum single item:"))
    max_item_combo = QComboBox()
    max_item_combo.addItem("No limit", 0.0)
    for price in sorted(set(cost for _, cost in AUTODRAINER_URLS)):
        max_item_combo.addItem(f"${price:.2f}", price)
    layout.addWidget(max_item_combo)

    btn = QPushButton("Next")
    btn.clicked.connect(dlg.accept)
    layout.addWidget(btn)

    if dlg.exec() != QDialog.Accepted:
        return

    behaviors["enabled"]["autodrainer"] = chk_enable.isChecked()
    behaviors["autodrainer"]["max_per_day_usd"] = budget_spin.value()
    behaviors["autodrainer"]["max_item_usd"] = max_item_combo.currentData()

    save_behaviors(config_dir, behaviors)
    behavior_manager.update_behaviors(behaviors)
    
def _step_work_mode(cfg, config_dir, save_config, behavior_manager, main_profile):
    dlg = QDialog(None)
    dlg.setWindowTitle("Work Mode")
    dlg.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    layout = QVBoxLayout(dlg)

    layout.addWidget(QLabel(
        "Would you like to set up a lighter profile for work hours~?\n"
        "This creates a schedule that switches automatically."
    ))

    btn_row = QHBoxLayout()
    btn_yes = QPushButton("Yes~")
    btn_skip = QPushButton("Skip")

    choice = {"setup": False}

    def pick_yes():
        choice["setup"] = True
        dlg.accept()

    def pick_skip():
        choice["setup"] = False
        dlg.accept()

    btn_yes.clicked.connect(pick_yes)
    btn_skip.clicked.connect(pick_skip)
    btn_row.addWidget(btn_yes)
    btn_row.addWidget(btn_skip)
    layout.addLayout(btn_row)

    dlg.exec()

    if not choice["setup"]:
        return

    # Ask for work hours
    time_dlg = QDialog(None)
    time_dlg.setWindowTitle("Work Hours")
    time_dlg.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    time_layout = QVBoxLayout(time_dlg)

    time_layout.addWidget(QLabel("When do your work hours start and end~?"))

    start_row = QHBoxLayout()
    start_row.addWidget(QLabel("Start:"))
    start_h = StepSpinBox(min_val=0, max_val=23, suffix="h", parent=time_dlg)
    start_h.setValue(9)
    start_row.addWidget(start_h)
    time_layout.addLayout(start_row)

    end_row = QHBoxLayout()
    end_row.addWidget(QLabel("End:"))
    end_h = StepSpinBox(min_val=0, max_val=23, suffix="h", parent=time_dlg)
    end_h.setValue(17)
    end_row.addWidget(end_h)
    time_layout.addLayout(end_row)

    btn_done = QPushButton("Set up")
    btn_done.clicked.connect(time_dlg.accept)
    time_layout.addWidget(btn_done)

    if time_dlg.exec() != QDialog.Accepted:
        return

    sh = int(start_h.value())
    eh = int(end_h.value())

    behaviors = load_behaviors(config_dir)
    behaviors["profiles"]["Work"] = build_work_profile(work_start_h=sh, work_end_h=eh)

    # Only create Mommy profile if it doesn't already exist
    if main_profile not in behaviors.get("profiles", {}):
        behaviors["profiles"][main_profile] = build_mommy_profile(tier=getattr(cfg, "tier", "free"))

    behaviors["schedule"] = build_work_schedule(
        work_start_h=sh, work_end_h=eh,
        main_profile=main_profile,
    )
    behaviors["schedule_enabled"] = True

    save_behaviors(config_dir, behaviors)
    behavior_manager.update_behaviors(behaviors)
    
def _step_operation_settings_1(cfg, save_config):
    dlg = QDialog(None)
    dlg.setWindowTitle("Display Settings")
    dlg.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    layout = QVBoxLayout(dlg)

    # Session receive mode
    layout.addWidget(QLabel("How should incoming sessions be handled?"))
    mode_combo = QComboBox()
    mode_combo.addItem("Full content warning (details + confirm)", "full")
    mode_combo.addItem("Confirm only (no details)", "minimal")
    mode_combo.addItem("Run immediately", "off")
    layout.addWidget(mode_combo)

    # Monitor selection
    screens = QApplication.screens()
    layout.addWidget(QLabel(f"Which monitors should show popups? ({len(screens)} detected)"))
    screen_checks = []
    for i, screen in enumerate(screens):
        chk = QCheckBox(f"Screen {i} — {screen.name()}")
        chk.setChecked(True)
        layout.addWidget(chk)
        screen_checks.append((i, chk))

    # Image opacity
    from PySide6.QtWidgets import QDoubleSpinBox
    layout.addWidget(QLabel("Image popup opacity:"))
    opacity_spin = QDoubleSpinBox()
    opacity_spin.setRange(0.0, 1.0)
    opacity_spin.setSingleStep(0.05)
    opacity_spin.setDecimals(2)
    opacity_spin.setValue(getattr(cfg, "image_popup_opacity", 1.0))
    layout.addWidget(opacity_spin)

    # Click-through
    chk_click = QCheckBox("Click-through image popups (can't close by clicking)")
    chk_click.setChecked(getattr(cfg, "image_click_through", False))
    layout.addWidget(chk_click)

    btn = QPushButton("Next")
    btn.clicked.connect(dlg.accept)
    layout.addWidget(btn)

    if dlg.exec() != QDialog.Accepted:
        return

    cfg.session_receive_mode = mode_combo.currentData()
    set_session_receive_mode(cfg.session_receive_mode)

    selected = [i for i, chk in screen_checks if chk.isChecked()]
    cfg.popup_screens = selected if len(selected) < len(screens) else None
    set_popup_screens(cfg.popup_screens)

    cfg.image_popup_opacity = opacity_spin.value()
    set_image_popup_opacity(cfg.image_popup_opacity)

    cfg.image_click_through = chk_click.isChecked()
    set_image_click_through(cfg.image_click_through)

    save_config(cfg)
    
def _step_operation_settings_2(cfg, save_config, tray):
    dlg = QDialog(None)
    dlg.setWindowTitle("Audio & Saving")
    dlg.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    layout = QVBoxLayout(dlg)

    # Audio device
    layout.addWidget(QLabel("Which audio output should Mommy use~?"))
    audio_combo = QComboBox()
    # We need access to the audio manager's device list
    # Pull from tray's callback
    choices = tray._get_audio_choices()
    for label, dev_id in choices:
        audio_combo.addItem(label, dev_id)
    layout.addWidget(audio_combo)

    # Image saving
    chk_save = QCheckBox("Save popped-up images to disk")
    chk_save.setChecked(getattr(cfg, "image_save_enabled", True))
    layout.addWidget(chk_save)

    btn = QPushButton("Next")
    btn.clicked.connect(dlg.accept)
    layout.addWidget(btn)

    if dlg.exec() != QDialog.Accepted:
        return

    dev_id = audio_combo.currentData()
    cfg.audio_device_id = dev_id
    tray.audio_device_changed.emit(dev_id)

    cfg.image_save_enabled = chk_save.isChecked()
    set_image_save_enabled(cfg.image_save_enabled)
    tray.set_image_save_enabled_checked(cfg.image_save_enabled)

    save_config(cfg)
    
def _step_linux_settings(cfg, save_config):
    dlg = QDialog(None)
    dlg.setWindowTitle("Linux Settings")
    dlg.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    layout = QVBoxLayout(dlg)

    layout.addWidget(QLabel(
        "Mommy needs to know how to change your wallpaper~\n\n"
        "Use {path} where the file path goes.\n"
        "Leave blank if you don't want wallpaper changes."
    ))

    layout.addWidget(QLabel("Set wallpaper command:"))
    set_field = QLineEdit()
    set_field.setPlaceholderText("e.g. feh --bg-fill {path}")
    layout.addWidget(set_field)

    layout.addWidget(QLabel("Get current wallpaper command (optional, enables restore):"))
    get_field = QLineEdit()
    get_field.setPlaceholderText("e.g. gsettings get org.gnome.desktop.background picture-uri")
    layout.addWidget(get_field)

    btn = QPushButton("Done~")
    btn.clicked.connect(dlg.accept)
    layout.addWidget(btn)

    if dlg.exec() != QDialog.Accepted:
        return

    set_cmd = (set_field.text() or "").strip() or None
    get_cmd = (get_field.text() or "").strip() or None

    cfg.wallpaper_set_cmd = set_cmd
    cfg.wallpaper_get_cmd = get_cmd
    save_config(cfg)
    set_wallpaper_set_cmd(set_cmd)
    set_wallpaper_get_cmd(get_cmd)
    
def _step_final_message():
    dlg = QDialog(None)
    dlg.setWindowTitle("All set~")
    dlg.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    layout = QVBoxLayout(dlg)

    layout.addWidget(QLabel(
        "You're all set up, darling~\n\n"
        "Mommy's app is now running in your system tray — "
        "look for the icon in the bottom right corner of your screen "
        "(you may need to click the little arrow to see it)~\n\n"
        "If you're not sure what to do next, check the FAQ on the server~"
    ))

    btn = QPushButton("Yes Mommy~")
    btn.clicked.connect(dlg.accept)
    layout.addWidget(btn)

    dlg.exec()

def run_onboarding(*, cfg, config_dir: Path, save_config, behavior_manager,
                   tray, content_roots, compiler, tier_signal=None):
    """
    Runs the full onboarding sequence.
    Call after enrollment completes and the main UI is wired up.
    
    tier_signal: a Signal that fires with (str) when tier changes,
                 used for the paid-waiting dialog.
    """
    results = {}

    if not _step_pet_names(cfg, save_config):
        return
    
    tier = _step_tier_check(cfg, save_config, tier_signal)
    results["tier"] = tier

    used_mommy_settings = _step_mommy_settings(cfg, config_dir, save_config,
                                                behavior_manager, tier)
    
    _step_required_tags(cfg, config_dir, save_config, behavior_manager)

    if used_mommy_settings:
        main_profile = "Mommy"
    else:
        main_profile = "Main"
        _step_behavior_basics(cfg, config_dir, save_config, behavior_manager,
                              profile_name=main_profile)

    if tier == "paid":
        _step_autodrainer(cfg, config_dir, save_config, behavior_manager)

    _step_work_mode(cfg, config_dir, save_config, behavior_manager, main_profile)

    if not used_mommy_settings:
        _step_operation_settings_1(cfg, save_config)

    _step_operation_settings_2(cfg, save_config, tray)

    if not sys.platform.startswith("win"):
        _step_linux_settings(cfg, save_config)
        
    _step_final_message()