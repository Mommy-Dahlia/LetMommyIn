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

BEHAVIORS_VERSION = 1

DEFAULT_BEHAVIORS = {
    "version": BEHAVIORS_VERSION,
    "active_time": {
        "start_h": 8,
        "start_m": 0,
        "end_h": 23,
        "end_m": 0
    },
    "general_frequency": {
        "min_minutes": 30,
        "random_minutes": 15,
    },
    "enabled": {
        "toys_and_teases": True,
        "rules_and_tasks": False,
        "web_aided_tasks": False,
        "bunny_bomb": False,
        "autodrainer": False,
        "session": False,
    },
    "autodrainer": {
        "max_per_day_usd": 0.0,
    },
    "session": {
        "allowed_sessions": [],
    },
    "bunny_bomb": {
        "audio_and_overlay": False,
    },
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
    
def _next_interval_ms(behaviors: dict) -> int:
    freq = behaviors.get("general_frequency", {})
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
        dispatch_command: Callable[[dict], None],
        get_session_path: Callable[[str], Path | None],
    ):
        super().__init__()
        self._config_dir = config_dir
        self._session_runner = session_runner
        self._dispatch_command = dispatch_command
        self._get_session_path = get_session_path
        self._behaviors: dict = {}

        self._general_timer = QTimer(self)
        self._general_timer.setSingleShot(True)
        self._general_timer.timeout.connect(self._on_general_tick)

        self._autodrainer_timer = QTimer(self)
        self._autodrainer_timer.setSingleShot(True)
        self._autodrainer_timer.timeout.connect(self._on_autodrainer_tick)
        
        self._drain_sequence: list[str] = []
        self._drain_index: int = 0
        self._drain_date: str = ""
        
    def start(self) -> None:
        self._behaviors = load_behaviors(self._config_dir)
        self._schedule_general()
        self._schedule_autodrainer()
     
    def reload(self) -> None:
        self._behaviors = load_behaviors(self._config_dir)
        self._schedule_general()
        self._schedule_autodrainer()
    
    def update_behaviors(self, behaviors: dict) -> None:
        self._behaviors = behaviors
        save_behaviors(self._config_dir, behaviors)
        self._schedule_general()
        self._schedule_autodrainer()
    
    def _schedule_general(self) -> None:
        self._general_timer.stop()
        enabled = self._behaviors.get("enabled", {})
        any_enabled = any([
            enabled.get("toys_and_teases"),
            enabled.get("rules_and_tasks"),
            enabled.get("web_aided_tasks"),
            enabled.get("bunny_bomb"),
            enabled.get("session"),
        ])
        if any_enabled:
            self._general_timer.start(_next_interval_ms(self._behaviors))
    
    def _on_general_tick(self) -> None:
        if not self._session_runner.is_active():
            if _in_active_time(self._behaviors):
                self._fire_general()
            self._schedule_general()
        else:
            # session is running, check again in 5 minutes
            self._general_timer.start(5 * 60 * 1000)
    
    def _schedule_autodrainer(self) -> None:
        self._autodrainer_timer.stop()
        if not self._behaviors.get("enabled", {}).get("autodrainer"):
            return
        if not AUTODRAINER_URLS:
            return
        
        max_usd = float(self._behaviors.get("autodrainer", {}).get("max_per_day_usd", 0.0))
        
        today = time.strftime("%Y-%m-%d")
        if self._drain_date != today:
            # only generate a new sequence if it's a new day
            max_usd = float(self._behaviors.get("autodrainer", {}).get("max_per_day_usd", 0.0))
            self._drain_sequence = _generate_drain_sequence(AUTODRAINER_URLS, max_usd)
            self._drain_index = 0
            self._drain_date = today
        
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
        enabled = self._behaviors.get("enabled", {})
        candidates = []
        
        pools = {
            "toys_and_teases": load_content_pool(self._config_dir, "toys_and_teases"),
            "rules_and_tasks": load_content_pool(self._config_dir, "rules_and_tasks"),
            "web_aided_tasks": load_content_pool(self._config_dir, "web_aided_tasks"),
        }
        
        for name in ("toys_and_teases", "rules_and_tasks", "web_aided_tasks"):
            if enabled.get(name) and pools[name]:
                candidates.append(name)
            
        if enabled.get("bunny_bomb"):
            candidates.append("bunny_bomb")
            
        if enabled.get("session") and self._behaviors.get("session", {}).get("allowed_sessions"):
            candidates.append("session")
            
        return candidates
    
    def _fire_general(self) -> None:
        candidates = self._enabled_general_behaviors()
        if not candidates:
            return
        choice = random.choice(candidates)
        getattr(self, f"_do_{choice}")()
    
    def _do_rules_and_tasks(self) -> None:
        pool = load_content_pool(self._config_dir, "rules_and_tasks")
        if not pool:
            return
        entry = random.choice(pool)
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
        pool = load_content_pool(self._config_dir, "web_aided_tasks")
        if not pool:
            return
        entry = random.choice(pool)
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
        
        try:
            import TheFactory
            images = TheFactory.load_images(str(Path(__file__).parent / "images.csv"))
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
    
    def _fire_autodrainer(self) -> None:
        if not self._drain_sequence:
            return
        if self._drain_index >= len(self._drain_sequence):
            # sequence exhausted for today, don't reschedule
            return
        
        url = self._drain_sequence[self._drain_index]
        self._drain_index += 1
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

