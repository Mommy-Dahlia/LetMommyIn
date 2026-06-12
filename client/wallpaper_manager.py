from __future__ import annotations
import os
import sys
import shutil
import random
import logging
from pathlib import Path
from typing import Optional

if sys.platform.startswith("win"):
    import ctypes
    from ctypes import wintypes

    SPI_GETDESKWALLPAPER = 0x0073
    SPI_SETDESKWALLPAPER = 0x0014
    SPIF_UPDATEINIFILE = 0x01
    SPIF_SENDCHANGE = 0x02
    
    def _get_wallpaper() -> str:
        buf = ctypes.create_unicode_buffer(512)
        ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETDESKWALLPAPER, len(buf), buf, 0
        )
        return buf.value
    
    def _set_wallpaper(path: str) -> bool:
        result = ctypes.windll.user32.SystemParametersInfoW(
            SPI_SETDESKWALLPAPER, 0, path,
            SPIF_UPDATEINIFILE | SPIF_SENDCHANGE
        )
        return bool(result)
    
else:
    import subprocess

    def _get_wallpaper_custom(cmd: str) -> str:
        if not cmd:
            return ""
        try:
            result = subprocess.run(
                cmd, shell=True,
                capture_output=True, text=True, timeout=5,
            )
            return result.stdout.strip()
        except Exception as e:
            logging.warning("Wallpaper get command failed: %s", e)
            return ""

    def _set_wallpaper_custom(cmd_template: str, path: str) -> bool:
        if not cmd_template:
            logging.warning("No wallpaper set command configured")
            return False
        cmd = cmd_template.replace("{path}", path)
        try:
            subprocess.run(cmd, shell=True, timeout=5, check=True)
            return True
        except Exception as e:
            logging.warning("Wallpaper set command failed: %s", e)
            return False
    
class WallpaperManager:
    def __init__(self, *, config_dir: Path, content_roots: list[Path],
                 custom_set_cmd: str | None = None, custom_get_cmd: str | None = None):
        self._config_dir = config_dir
        self._content_roots = content_roots
        self._persistent_dir = config_dir / "wallpapers"
        self._persistent_dir.mkdir(parents=True, exist_ok=True)
        self._original_path: Optional[str] = None
        self._state_path = config_dir / "wallpaper_original.txt"
        self._load_saved_original()
        
    def _load_saved_original(self) -> None:
        if self._state_path.exists():
            try:
                self._original_path = self._state_path.read_text(encoding="utf-8").strip()
            except Exception:
                pass

    def _save_original(self, path: str) -> None:
        self._original_path = path
        self._state_path.write_text(path, encoding="utf-8")
        
    def _find_pool(self) -> list[Path]:
        images: list[Path] = []
        for root in self._content_roots:
            wp_dir = root / "wallpapers"
            if not wp_dir.is_dir():
                continue
            for p in wp_dir.iterdir():
                if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp"):
                    images.append(p)
        return images
    
    def _get_current(self) -> str:
        if sys.platform.startswith("win"):
            return _get_wallpaper()
        return _get_wallpaper_custom(self._custom_get_cmd or "")

    def _set(self, path: str) -> bool:
        if sys.platform.startswith("win"):
            return _set_wallpaper(path)
        return _set_wallpaper_custom(self._custom_set_cmd or "", path)
    
    def change(self) -> bool:
        pool = self._find_pool()
        if not pool:
            logging.warning("WallpaperManager: no images found in wallpapers/")
            return False

        pick = random.choice(pool)
        dest = self._persistent_dir / pick.name
        shutil.copy2(pick, dest)

        if self._original_path is None:
            current = self._get_current()
            if current:
                self._save_original(current)

        return self._set(str(dest))
    
    def restore(self) -> bool:
        if not self._original_path:
            logging.info("WallpaperManager: no original wallpaper saved")
            return False

        result = self._set(self._original_path)
        if result:
            self._original_path = None
            try:
                self._state_path.unlink(missing_ok=True)
            except Exception:
                pass
        return result
    
    def has_original(self) -> bool:
        return self._original_path is not None