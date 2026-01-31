# ui_settings.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

@dataclass
class UiSettings:
    popup_screens: list[int] | None = None  # None = all

_SETTINGS = UiSettings()

def set_popup_screens(indices: list[int] | None) -> None:
    _SETTINGS.popup_screens = indices

def get_popup_screens() -> list[int] | None:
    return _SETTINGS.popup_screens
