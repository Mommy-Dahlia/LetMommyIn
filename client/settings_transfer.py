import json
import zlib
import base64
from pathlib import Path
from behavior_manager import load_behaviors, save_behaviors, FREE_BEHAVIORS


def gather_export_payload(cfg, config_dir: Path) -> dict:
    behaviors = load_behaviors(config_dir)

    sessions = {}
    sessions_dir = config_dir / "content" / "sessions"
    if sessions_dir.is_dir():
        for p in sessions_dir.glob("*.json"):
            if p.name.endswith(".meta.json"):
                continue
            try:
                sessions[p.stem] = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass

    return {
        "tier": getattr(cfg, "tier", "free"),
        "config": {
            "pet_names": cfg.pet_names,
            "popup_screens": cfg.popup_screens,
            "default_audio_url": cfg.default_audio_url,
            "default_overlay_url": cfg.default_overlay_url,
            "default_overlay_opacity": cfg.default_overlay_opacity,
            "default_overlay_screen": cfg.default_overlay_screen,
            "popup_sfx_path": cfg.popup_sfx_path,
            "image_save_enabled": cfg.image_save_enabled,
            "image_popup_opacity": getattr(cfg, "image_popup_opacity", 1.0),
            "image_popup_scale": getattr(cfg, "image_popup_scale", 1.0),
            "image_click_through": getattr(cfg, "image_click_through", False),
            "session_receive_mode": getattr(cfg, "session_receive_mode", "full"),
            "session_speed": getattr(cfg, "session_speed", 1.0),
        },
        "behaviors": behaviors,
        "sessions": sessions,
    }


def export_settings(cfg, config_dir: Path) -> str:
    payload = gather_export_payload(cfg, config_dir)
    raw = json.dumps(payload, separators=(",", ":"))
    compressed = zlib.compress(raw.encode("utf-8"), level=9)
    return base64.urlsafe_b64encode(compressed).decode("ascii")


def decode_settings(code: str) -> dict:
    compressed = base64.urlsafe_b64decode(code.encode("ascii"))
    raw = zlib.decompress(compressed).decode("utf-8")
    return json.loads(raw)


def apply_imported_settings(payload: dict, cfg, config_dir: Path, save_config):
    # --- Config ---
    config_data = payload.get("config", {})
    for key, val in config_data.items():
        if hasattr(cfg, key):
            setattr(cfg, key, val)
    save_config(cfg)

    # --- Behaviors ---
    behaviors = payload.get("behaviors", {})
    save_behaviors(config_dir, behaviors)

    # --- Sessions ---
    sessions = payload.get("sessions", {})
    sessions_dir = config_dir / "content" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    for stem, session_data in sessions.items():
        out = sessions_dir / f"{stem}.json"
        out.write_text(json.dumps(session_data, indent=2), encoding="utf-8")