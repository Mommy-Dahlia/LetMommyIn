# ui_settings.py
from __future__ import annotations
from dataclasses import dataclass

@dataclass
class AppSettings:
    # UI / placement
    popup_screens: list[int] | None = None  # None = all

    # Personalization
    pet_names: list[str] | None = None      # tokens for #PNS replacement

    # Defaults for sessions (client-side policy)
    default_audio_url: str | None = None

    default_overlay_url: str | None = None
    default_overlay_opacity: float = 1.0
    default_overlay_screen: int = -1        # -1 = all screens
    
    popup_sfx_path: str | None = None  # local file path to .wav/.ogg, etc.
    
    image_save_enabled: bool = True
    image_save_dir: str | None = None
    
    session_receive_mode: str = "full"  # "full" | "minimal" | "off"


_SETTINGS = AppSettings()

# --- popup screens ---
def set_popup_screens(indices: list[int] | None) -> None:
    _SETTINGS.popup_screens = indices

def get_popup_screens() -> list[int] | None:
    return _SETTINGS.popup_screens

# --- pet names ---
def set_pet_names(names: list[str] | None) -> None:
    _SETTINGS.pet_names = names

def get_pet_names() -> list[str] | None:
    return _SETTINGS.pet_names

# --- default audio ---
def set_default_audio_url(url: str | None) -> None:
    _SETTINGS.default_audio_url = (url or "").strip() or None

def get_default_audio_url() -> str | None:
    return _SETTINGS.default_audio_url

# --- overlay defaults ---
def set_default_overlay(url: str | None, *, opacity: float = 1.0, screen: int = -1) -> None:
    _SETTINGS.default_overlay_url = (url or "").strip() or None
    try:
        _SETTINGS.default_overlay_opacity = max(0.0, min(1.0, float(opacity)))
    except Exception:
        _SETTINGS.default_overlay_opacity = 1.0
    try:
        _SETTINGS.default_overlay_screen = int(screen)
    except Exception:
        _SETTINGS.default_overlay_screen = -1

def get_default_overlay() -> tuple[str | None, float, int]:
    return (_SETTINGS.default_overlay_url, _SETTINGS.default_overlay_opacity, _SETTINGS.default_overlay_screen)

def set_popup_sfx_path(path: str | None) -> None:
    _SETTINGS.popup_sfx_path = (path or "").strip() or None

def get_popup_sfx_path() -> str | None:
    return _SETTINGS.popup_sfx_path

def set_image_save_enabled(v: bool) -> None:
    _SETTINGS.image_save_enabled = bool(v)

def get_image_save_enabled() -> bool:
    return bool(_SETTINGS.image_save_enabled)

def set_image_save_dir(path: str | None) -> None:
    _SETTINGS.image_save_dir = (path or "").strip() or None

def get_image_save_dir() -> str | None:
    return _SETTINGS.image_save_dir

def set_session_receive_mode(mode: str) -> None:
    mode = (mode or "").strip().lower()
    if mode not in ("full", "minimal", "off"):
        mode = "full"
    _SETTINGS.session_receive_mode = mode

def get_session_receive_mode() -> str:
    return (_SETTINGS.session_receive_mode or "full").strip().lower()

