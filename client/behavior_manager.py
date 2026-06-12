from __future__ import annotations
import json
import random
import time
import logging
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout

from session_runner import _apply_pns
from session_compiler import resource_path

BEHAVIORS_VERSION = 2

FREE_BEHAVIORS = {"toys_and_teases", "wfm", "bunny_bomb"}

DEFAULT_BEHAVIORS = {
    "version": BEHAVIORS_VERSION,
    "active_time": {
        "start_h": 0,
        "start_m": 0,
        "end_h": 23,
        "end_m": 59
    },
    "general_frequency": {
        "min_minutes": 30,
        "random_minutes": 15,
    },
    "enabled": {
        "toys_and_teases": True,
        "rules_and_tasks": False,
        "web_aided_tasks": False,
        "wfm": False,
        "either_or": False,
        "bunny_bomb": False,
        "autodrainer": False,
        "session": False,
        "wallpaper": False,
    },
    "autodrainer": {
        "max_per_day_usd": 0.0,
        "max_item_usd": 0.0,
    },
    "session": {
        "allowed_sessions": [],
    },
    "bunny_bomb": {
        "audio_and_overlay": False,
    },
    "allowed_tags": [],\
    "seen_tags": [],
    "behavior_weights": {},
    "tag_weights": {},
    "profiles": {},
    "active_profile": None,
    "schedule": [],
    "schedule_enabled": False,
}

def _merge_defaults(data: dict, defaults: dict) -> None:
    for key, val in defaults.items():
        if key not in data:
            data[key] = val
        elif isinstance(val, dict) and isinstance(data[key], dict):
            _merge_defaults(data[key], val)
            
def load_behaviors(config_dir: Path) -> dict:
    path = config_dir / "behaviors.json"
    if not path.exists():
        save_behaviors(config_dir, DEFAULT_BEHAVIORS)
        return dict(DEFAULT_BEHAVIORS)
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        _merge_defaults(data, DEFAULT_BEHAVIORS)
        return data
    except Exception:
        return dict(DEFAULT_BEHAVIORS)

def save_behaviors(config_dir: Path, behaviors: dict) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / "behaviors.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(behaviors, f, indent=2)
        
def load_content_pool(config_dir: Path, name: str) -> list:
    path = config_dir / "content" / "behaviors" / f"{name}.json"
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        logging.warning("Failed to load behavior pool: %s", name)
        return []
    
def save_content_pool(config_dir: Path, name: str, pool: list) -> None:
    out_dir = config_dir / "content" / "behaviors"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(pool, f, indent=2, ensure_ascii=False)
        
def _resolve_scheduled_profile(behaviors: dict) -> str | None:
    if not behaviors.get("schedule_enabled"):
        return None
    schedule = behaviors.get("schedule", [])
    if not schedule:
        return None

    t = time.localtime()
    current = t.tm_hour * 60 + t.tm_min

    best = None
    best_start = -1

    for slot in schedule:
        start = int(slot.get("start_h", 0)) * 60 + int(slot.get("start_m", 0))
        if start <= current and start > best_start:
            best_start = start
            best = slot.get("profile")

    # Handle wrap-around: if no slot has fired yet today,
    # the latest slot from "yesterday" is still active
    if best is None:
        for slot in schedule:
            start = int(slot.get("start_h", 0)) * 60 + int(slot.get("start_m", 0))
            if start > best_start:
                best_start = start
                best = slot.get("profile")

    return best
        
def _in_active_time(behaviors: dict) -> bool:
    active = behaviors.get("active_time", {})
    start_h = int(active.get("start_h", 8))
    start_m = int(active.get("start_m", 0))
    end_h = int(active.get("end_h", 23))
    end_m = int(active.get("end_m", 0))

    t = time.localtime()
    current = t.tm_hour * 60 + t.tm_min
    start = start_h * 60 + start_m
    end = end_h * 60 + end_m

    if start <= end:
        return start <= current < end
    else:
        return current >= start or current < end
    
def _next_interval_ms(freq: dict) -> int:
    min_m = float(freq.get("min_minutes", 30))
    rnd_m = float(freq.get("random_minutes", 15))
    total_minutes = min_m + random.uniform(0, rnd_m)
    return int(total_minutes * 60 * 1000)

def _generate_drain_sequence(url_pool: list[tuple[str, float]], max_usd: float) -> list[str]:
    if not url_pool or max_usd <= 0:
        return []
    
    sequence = []
    spent = 0.0
    
    while True:
        # filter to urls we can still afford
        affordable = [(url, cost) for url, cost in url_pool if spent + cost <= max_usd]
        if not affordable:
            break
        url, cost = random.choice(affordable)
        sequence.append(url)
        spent += cost
    
    return sequence

def _autodrainer_interval_ms(behaviors: dict) -> int:
    return random.randint(30 * 60 * 1000, 60 * 60 * 1000)

def load_drain_state(config_dir: Path) -> tuple[str, int, list[str]]:
    path = config_dir / "drain_state.json"
    if not path.exists():
        return ("", 0, [])
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return (
            str(data.get("date", "")),
            int(data.get("index", 0)),
            list(data.get("sequence", [])),
        )
    except Exception:
        return ("", 0, [])

def save_drain_state(config_dir: Path, date: str, index: int, sequence: list[str]) -> None:
    path = config_dir / "drain_state.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump({"date": date, "index": index, "sequence": sequence}, f)

class BehaviorSessionDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Incoming~")
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Mommy wants to pick out one of your sessions for you~ Would you like to begin?"))
        btn_row = QHBoxLayout()
        btn_yes = QPushButton("Yes Mommy~")
        btn_no = QPushButton("Not right now")
        btn_yes.clicked.connect(self.accept)
        btn_no.clicked.connect(self.reject)
        btn_row.addWidget(btn_yes)
        btn_row.addWidget(btn_no)
        layout.addLayout(btn_row)

class RulesTaskCheckDialog(QDialog):
    def __init__(self, check_text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Check in~")
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(_apply_pns(check_text)))
        btn_row = QHBoxLayout()
        btn_yes = QPushButton("Yes Mommy~")
        btn_no = QPushButton("No Mommy...")
        btn_yes.clicked.connect(self.accept)
        btn_no.clicked.connect(self.reject)
        btn_row.addWidget(btn_yes)
        btn_row.addWidget(btn_no)
        layout.addLayout(btn_row)
        
class EitherOrDialog(QDialog):
    def __init__(self, task_a: str, task_b: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pick one~")
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.chosen = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(_apply_pns("Mommy has two tasks for you, #PNS~ Pick one~")))

        btn_row = QHBoxLayout()
        btn_a = QPushButton(task_a)
        btn_b = QPushButton(task_b)

        def pick_a():
            self.chosen = task_a
            self.accept()

        def pick_b():
            self.chosen = task_b
            self.accept()

        btn_a.clicked.connect(pick_a)
        btn_b.clicked.connect(pick_b)
        btn_row.addWidget(btn_a)
        btn_row.addWidget(btn_b)
        layout.addLayout(btn_row)

AUTODRAINER_URLS: list[tuple[str, float]] = [
    ("https://throne.com/obeymommydahlia/item/6001ba41-5db7-4ab0-b5c7-31ad4bef2da4", 1.00),
    ("https://throne.com/obeymommydahlia/item/7f32d184-c11c-40df-ba0c-0a326443ec1c", 5.00),
    ("https://throne.com/obeymommydahlia/item/d208a2ec-a83a-4b73-9ef0-a58a8977bde4", 10.00),
    ("https://throne.com/obeymommydahlia/item/17090602-7d39-44a9-b855-4e590cf2ad67", 25.00),
    ("https://throne.com/obeymommydahlia/item/074de71c-50e0-4a31-a198-ebfba7700ae6", 50.00),
    ("https://throne.com/obeymommydahlia/item/f6d9aaf2-048a-4dae-8397-da6dd92f4113", 100.00),
    ("https://throne.com/obeymommydahlia/item/bd2e367b-3844-4527-8a24-4dccc04b91fc", 500.00),
]

class BehaviorManager(QObject):
    def __init__(
        self,
        *,
        config_dir: Path,
        session_runner,
        wfm_manager,
        wallpaper_manager,
        dispatch_command: Callable[[dict], None],
        get_session_path: Callable[[str], Path | None],
        get_tier: Callable[[], str],
    ):
        super().__init__()
        self._config_dir = config_dir
        self._session_runner = session_runner
        self._wfm_manager = wfm_manager
        self._wallpaper_manager = wallpaper_manager
        self._dispatch_command = dispatch_command
        self._get_session_path = get_session_path
        self._get_tier = get_tier
        self._behaviors: dict = {}

        self._general_timer = QTimer(self)
        self._general_timer.setSingleShot(True)
        self._general_timer.timeout.connect(self._on_general_tick)

        self._autodrainer_timer = QTimer(self)
        self._autodrainer_timer.setSingleShot(True)
        self._autodrainer_timer.timeout.connect(self._on_autodrainer_tick)
        
        self._schedule_timer = QTimer(self)
        self._schedule_timer.timeout.connect(self._check_schedule)
        
        self._drain_sequence: list[str] = []
        self._drain_index: int = 0
        self._drain_date: str = ""
        
    def _entry_allowed(self, entry: dict) -> bool:
        tags = set(entry.get("tags", []))
        if not tags:
            return False
        allowed = set(self._behaviors.get("allowed_tags", []))
        return bool(tags & allowed)
        
    def start(self) -> None:
        self._behaviors = load_behaviors(self._config_dir)
        self._drain_date, self._drain_index, self._drain_sequence = load_drain_state(self._config_dir)
        self._schedule_timer.start(60 * 1000)
        self._check_schedule()
        self._schedule_general()
        self._schedule_autodrainer()
     
    def reload(self) -> None:
        self._behaviors = load_behaviors(self._config_dir)
        self._check_schedule()
        self._schedule_general()
        self._schedule_autodrainer()
    
    def update_behaviors(self, behaviors: dict) -> None:
        self._behaviors = behaviors
        save_behaviors(self._config_dir, behaviors)
        self._schedule_general()
        self._schedule_autodrainer()
        
    def set_active_profile(self, name: str | None) -> None:
        if name and name not in self._behaviors.get("profiles", {}):
            logging.warning("BehaviorManager: unknown profile: %s", name)
            return
        self._behaviors["active_profile"] = name
        save_behaviors(self._config_dir, self._behaviors)
        self._schedule_general()
        self._schedule_autodrainer()
        
    def _check_schedule(self) -> None:
        resolved = _resolve_scheduled_profile(self._behaviors)
        current = self._behaviors.get("active_profile")
        if resolved != current:
            self.set_active_profile(resolved)
    
    def _schedule_general(self) -> None:
        self._general_timer.stop()
        enabled = self._effective_enabled()
        any_enabled = any([
            enabled.get("toys_and_teases"),
            enabled.get("rules_and_tasks"),
            enabled.get("web_aided_tasks"),
            enabled.get("bunny_bomb"),
            enabled.get("session"),
            enabled.get("wfm"),
            enabled.get("either_or"),
            enabled.get("wallpaper"),
        ])
        if any_enabled:
            self._general_timer.start(_next_interval_ms(self._effective_frequency()))
    
    def _on_general_tick(self) -> None:
        if not self._session_runner.is_active() and not self._wfm_manager.is_active():
            if _in_active_time(self._behaviors):
                self._fire_general()
            self._schedule_general()
        else:
            # session is running, check again in 5 minutes
            self._general_timer.start(5 * 60 * 1000)
    
    def _schedule_autodrainer(self) -> None:
        self._autodrainer_timer.stop()
        if self._get_tier() != "paid":
            return
        if not self._behaviors.get("enabled", {}).get("autodrainer"):
            return
        if not AUTODRAINER_URLS:
            return
        
        max_usd = float(self._behaviors.get("autodrainer", {}).get("max_per_day_usd", 0.0))
        
        today = time.strftime("%Y-%m-%d")
        if self._drain_date != today:
            # only generate a new sequence if it's a new day
            max_usd = float(self._behaviors.get("autodrainer", {}).get("max_per_day_usd", 0.0))
            max_item = float(self._behaviors.get("autodrainer", {}).get("max_item_usd", 0.0))
            pool = AUTODRAINER_URLS
            if max_item > 0:
                pool = [(url, cost) for url, cost in pool if cost <= max_item]
            self._drain_sequence = _generate_drain_sequence(AUTODRAINER_URLS, max_usd)
            self._drain_index = 0
            self._drain_date = today
            save_drain_state(self._config_dir, self._drain_date, self._drain_index, self._drain_sequence)
        
        if not self._drain_sequence:
            return
    
        if self._drain_index < len(self._drain_sequence):
            self._autodrainer_timer.start(_autodrainer_interval_ms(self._behaviors))
    
    def _on_autodrainer_tick(self) -> None:
        if not self._session_runner.is_active():
            if _in_active_time(self._behaviors):
                self._fire_autodrainer()
            if self._drain_index < len(self._drain_sequence):
                self._autodrainer_timer.start(_autodrainer_interval_ms(self._behaviors))
        else:
            self._autodrainer_timer.start(5 * 60 * 1000)
            
    
    def _enabled_general_behaviors(self) -> list[str]:
        enabled = self._effective_enabled()
        tier = self._get_tier()
        
        candidates = []
        
        pools = {
            "toys_and_teases": load_content_pool(self._config_dir, "toys_and_teases"),
            "rules_and_tasks": load_content_pool(self._config_dir, "rules_and_tasks"),
            "web_aided_tasks": load_content_pool(self._config_dir, "web_aided_tasks"),
            "wfm": load_content_pool(self._config_dir, "wfm"), 
            "either_or": load_content_pool(self._config_dir, "either_or"),
        }
        
        logging.info("Behavior pools: %s", {k: len(v) for k, v in pools.items()})
        logging.info("Enabled: %s", enabled)
        
        for name in ("toys_and_teases", "rules_and_tasks", "web_aided_tasks", "wfm", "either_or"):
            if enabled.get(name) and pools[name]:
                if tier == "paid" or name in FREE_BEHAVIORS:
                    candidates.append(name)
            
        if enabled.get("bunny_bomb"):
            if tier == "paid" or "bunny_bomb" in FREE_BEHAVIORS:
                candidates.append("bunny_bomb")
            
        if enabled.get("session") and self._behaviors.get("session", {}).get("allowed_sessions"):
            if tier == "paid" or "session" in FREE_BEHAVIORS:
                candidates.append("session")
            
        if enabled.get("wallpaper") and self._wallpaper_manager._find_pool():
            if tier == "paid" or "wallpaper" in FREE_BEHAVIORS:
                candidates.append("wallpaper")
            
        logging.info("Candidates: %s", candidates)
        return candidates
    
    def _effective_enabled(self) -> dict:
        profile_name = self._behaviors.get("active_profile")
        if profile_name:
            profile = self._behaviors.get("profiles", {}).get(profile_name)
            if profile:
                enabled = dict(profile.get("enabled", self._behaviors.get("enabled", {})))
                enabled["autodrainer"] = self._behaviors.get("enabled", {}).get("autodrainer", False)
                return enabled
        return self._behaviors.get("enabled", {})

    def _effective_behavior_weights(self) -> dict:
        profile_name = self._behaviors.get("active_profile")
        if profile_name:
            profile = self._behaviors.get("profiles", {}).get(profile_name)
            if profile:
                return profile.get("behavior_weights", {})
        return self._behaviors.get("behavior_weights", {})

    def _effective_tag_weights(self) -> dict:
        profile_name = self._behaviors.get("active_profile")
        if profile_name:
            profile = self._behaviors.get("profiles", {}).get(profile_name)
            if profile:
                return profile.get("tag_weights", {})
        return self._behaviors.get("tag_weights", {})
    
    def _effective_frequency(self) -> dict:
        profile_name = self._behaviors.get("active_profile")
        if profile_name:
            profile = self._behaviors.get("profiles", {}).get(profile_name)
            if profile and "general_frequency" in profile:
                return profile["general_frequency"]
        return self._behaviors.get("general_frequency", {})
    
    def _fire_general(self) -> None:
        candidates = self._enabled_general_behaviors()
        if not candidates:
            return
        weights = [self._behavior_weight(c) for c in candidates]
        if sum(weights) <= 0:
            return
        choice = random.choices(candidates, weights=weights, k=1)[0]
        getattr(self, f"_do_{choice}")()
    
    def _pick_from_pool(self, pool_name: str) -> dict | None:
        pool = load_content_pool(self._config_dir, pool_name)
        pool = [e for e in pool if self._entry_allowed(e)]
        if not pool:
            return None
        weights = [self._entry_weight(e) for e in pool]
        if sum(weights) <= 0:
            return None
        return random.choices(pool, weights=weights, k=1)[0]
        
    def _do_toys_and_teases(self) -> None:
        entry = self._pick_from_pool("toys_and_teases")
        if not entry:
            return
        messages = entry.get("messages", [])

        offset_ms = 0
        for msg in messages:
            text = str(msg.get("text", ""))
            delay_s = float(msg.get("delay_seconds", 5))

            QTimer.singleShot(
                offset_ms,
                lambda t=text, l=delay_s: self._dispatch_command({
                    "type": "show_message",
                    "title": "Let Mommy In",
                    "body": t,
                    "lifespan_s": l,
                })
            )
            offset_ms += int(delay_s * 1000)
    
    def _do_rules_and_tasks(self) -> None:
        entry = self._pick_from_pool("rules_and_tasks")
        if not entry:
            return
        task = str(entry.get("task", ""))
        check_text = str(entry.get("check_text", "Did you do what Mommy asked?"))
        timer_ms = int(float(entry.get("timer_minutes", 5)) * 60 * 1000)
        reward = str(entry.get("reward", ""))
        punishment = str(entry.get("punishment", ""))
        
        self._dispatch_command({
            "type": "show_message",
            "title": "Mommy has a task for you",
            "body": task,
            "lifespan_s": 30,
        })
        
        QTimer.singleShot(timer_ms, lambda: self._do_task_check(check_text, reward, punishment))
    
    def _do_task_check(self, check_text: str, reward: str, punishment: str) -> None:
        dlg = RulesTaskCheckDialog(check_text)
        if dlg.exec() == QDialog.Accepted:
            self._dispatch_command({
                "type": "show_message",
                "title": "Good #PNS",
                "body": reward,
                "lifespan_s": 15,
            })
        else:
            self._dispatch_command({
                "type": "show_message",
                "title": "Tsk tsk~",
                "body": punishment,
                "lifespan_s": 15,
            })
    
    def _do_web_aided_tasks(self) -> None:
        entry = self._pick_from_pool("web_aided_tasks")
        if not entry:
            return
        url = str(entry.get("url", "")).strip()
        msg = str(entry.get("message", "")).strip()
        
        if url:
            self._dispatch_command({"type": "open_url", "body": url})
        if msg:
            self._dispatch_command({
                "type": "show_message",
                "title": "Let Mommy In",
                "body": msg,
                "lifespan_s": 15,
            })
    
    def _do_bunny_bomb(self) -> None:
        bb_config = self._behaviors.get("bunny_bomb", {})
        audio_and_overlay = bool(bb_config.get("audio_and_overlay", False))
        
        csv_path = resource_path("content/images.csv")
            
        logging.info("BunnyBomb: looking for images.csv at %s", csv_path)
            
        try:
            import TheFactory
            images = TheFactory.load_images(csv_path)
            logging.info("BunnyBomb: loaded %d images", len(images))
        except Exception:
            logging.warning("BunnyBomb: failed to load images")
            return
        
        if not images:
            return
        
        count = random.randint(10, 40)
        picks = random.sample(images, min(count, len(images)))
        
        offset_ms = 0
        if audio_and_overlay:
            from parser import _apply_client_session_defaults
            steps = [{"type": "image_popup", "body": img["url"], "timer_s": 0} for img in picks]
            steps = _apply_client_session_defaults(steps)
            for step in steps:
                QTimer.singleShot(offset_ms, lambda s=step: self._dispatch_command(s))
                offset_ms += random.randint(3000, 5000)
        else:
            for img in picks:
                QTimer.singleShot(
                    offset_ms,
                    lambda url=img["url"]: self._dispatch_command({
                        "type": "image_popup",
                        "body": url,
                    })
                )
                offset_ms += random.randint(3000, 5000)
    
    def _do_session(self) -> None:
        allowed = self._behaviors.get("session", {}).get("allowed_sessions", [])
        if not allowed:
            return
        
        dlg = BehaviorSessionDialog()
        if dlg.exec() != QDialog.Accepted:
            return
        
        stem = random.choice(allowed)
        path = self._get_session_path(stem)
        if path is None:
            logging.warning("BehaviorManager: session not found: %s", stem)
            return
        
        from parser import _apply_client_session_defaults
        from session_compiler import SessionCompiler
        try:
            compiler = SessionCompiler(roots=[path.parent.parent])
            compiled = compiler.compile_steps(path)
            steps = _apply_client_session_defaults(compiled.steps)
            self._session_runner.start(
                session_id=f"behavior_{stem}",
                steps=steps,
            )
        except Exception:
            logging.exception("BehaviorManager: failed to run session: %s", stem)
            
    def _do_wfm(self) -> None:
        entry = self._pick_from_pool("wfm")
        if not entry:
            return
        text = str(entry.get("text", ""))
        if not text.strip():
            return
        self._dispatch_command({
            "type": "write_for_me",
            "text": text
        })
        
    def _do_either_or(self) -> None:
        entry = self._pick_from_pool("either_or")
        if not entry:
            return
        task_a = str(entry.get("task_a", ""))
        task_b = str(entry.get("task_b", ""))
        if not task_a.strip() or not task_b.strip():
            return

        dlg = EitherOrDialog(_apply_pns(task_a), _apply_pns(task_b))
        if dlg.exec() != QDialog.Accepted or dlg.chosen is None:
            return

        timer_ms = int(float(entry.get("timer_minutes", 5)) * 60 * 1000)
        reward = str(entry.get("reward", ""))
        def _check():
            dlg = RulesTaskCheckDialog(_apply_pns("Did you do what Mommy asked, #PNS~?"))
            if dlg.exec() == QDialog.Accepted and reward:
                self._dispatch_command({
                    "type": "show_message",
                    "title": "Good #PNS~",
                    "body": reward,
                    "lifespan_s": 15,
                })

        QTimer.singleShot(timer_ms, _check)
    
    def _do_wallpaper(self) -> None:
        if not self._wallpaper_manager.change():
            logging.warning("BehaviorManager: wallpaper change failed")
            
    def _behavior_weight(self, name: str) -> float:
       w = self._effective_behavior_weights().get(name, 1.0)
       return max(0.0, float(w))

    def _entry_weight(self, entry: dict) -> float:
        tag_weights = self._effective_tag_weights()
        tags = entry.get("tags", [])
        if not tags:
            return 1.0
        weight = 1.0
        for tag in tags:
            weight *= float(tag_weights.get(tag, 1.0))
        return max(0.0, weight)
        
    def _fire_autodrainer(self) -> None:
        if not self._drain_sequence:
            return
        if self._drain_index >= len(self._drain_sequence):
            # sequence exhausted for today, don't reschedule
            return
        
        url = self._drain_sequence[self._drain_index]
        self._drain_index += 1
        save_drain_state(self._config_dir, self._drain_date, self._drain_index, self._drain_sequence)
        self._dispatch_command({"type": "open_url", "body": url})
        
    def trigger_drain(self) -> None:
        today = time.strftime("%Y-%m-%d")
        if self._drain_date != today:
            # new day, generate fresh sequence first
            self._schedule_autodrainer()
        if self._drain_index < len(self._drain_sequence):
            self._fire_autodrainer()
            if self._drain_index < len(self._drain_sequence):
                self._autodrainer_timer.start(_autodrainer_interval_ms(self._behaviors))
        # if sequence exhausted for today, do nothing
    
    def trigger_next_event(self) -> None:
        self._fire_general()

