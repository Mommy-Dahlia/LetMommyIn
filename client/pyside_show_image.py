from PySide6.QtCore import Qt, QTimer, QThread, Signal, QObject
from PySide6.QtWidgets import QApplication, QDialog, QLabel
from PySide6.QtGui import QPixmap, QScreen, QIcon
from ui_settings import get_popup_screens, get_image_save_enabled, get_image_save_dir
import sys
import os
from PIL import Image
from PIL import ImageQt
import requests
import random
from io import BytesIO
from pathlib import Path
import hashlib
import time

def resource_path(relative: str) -> str:
    if hasattr(sys, "_MEIPASS"):
        return str(Path(sys._MEIPASS) / relative)
    return str(Path(__file__).parent / relative)


_ACTIVE_DIALOGS: list[QDialog] = []

ICON_PATH = resource_path("MommyIcon.ico")

class ImageLoader(QObject):
    loaded = Signal(object)
    failed = Signal()

    def __init__(self, url: str):
        super().__init__()
        self._url = url

    def run(self) -> None:
        try:
            resp = requests.get(self._url, timeout=5)
            if resp.status_code != 200:
                self.failed.emit()
                return
            img = Image.open(BytesIO(resp.content))
            try:
                alpha = img.getchannel('A')
                bbox = alpha.getbbox()
                img = img.crop(bbox)
            except ValueError:
                pass
            img.thumbnail((800, 800), Image.Resampling.LANCZOS)
            pixmap = ImageQt.toqpixmap(img)
            self.loaded.emit(pixmap)
        except Exception:
            self.failed.emit()

class ImagePopup(QDialog):
    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self._url = url
        self._thread = None
        self._loader = None
        self.initUI(url)

    def show_on_top(self) -> None:
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.show()
        self.raise_()
        self.activateWindow()

    def mousePressEvent(self, event):
        match event.button():
            case Qt.RightButton:
                self.dragPosition = event.globalPosition().toPoint() - \
                    self.frameGeometry().topLeft()
                event.accept()
            case Qt.LeftButton:
                self.close()
                event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.RightButton:
            self.move(event.globalPosition().toPoint() - self.dragPosition)
            event.accept()

    def initUI(self, url):
        self.setWindowTitle("Image Popup")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setStyleSheet("background: #e8d9f1;")
        if os.path.exists(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))

        self.label = QLabel(self)

        # pick screen before image loads
        screens = QApplication.screens()
        allowed = get_popup_screens()
        if allowed is None or not allowed:
            self._screm = random.choice(screens)
        else:
            idx = random.choice(
                [i for i in allowed if 0 <= i < len(screens)]
            ) if any(0 <= i < len(screens) for i in allowed) else 0
            self._screm = screens[idx]

        # start async load
        self._thread = QThread()
        self._loader = ImageLoader(url)
        self._loader.moveToThread(self._thread)
        self._thread.started.connect(self._loader.run)
        self._loader.loaded.connect(self._on_loaded)
        self._loader.failed.connect(self.close)
        self._loader.loaded.connect(self._thread.quit)
        self._loader.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _on_loaded(self, pixmap) -> None:
        geom = self._screm.availableGeometry()
        max_w = int(geom.width() * 0.6)
        max_h = int(geom.height() * 0.6)

        scaled = pixmap.scaled(
            max_w, max_h,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )
        self.label.setPixmap(scaled)
        self.label.resize(scaled.width(), scaled.height())
        self.resize(scaled.width(), scaled.height())

        frmX = geom.x() + random.randint(0, max(0, geom.width() - self.width()))
        frmY = geom.y() + random.randint(0, max(0, geom.height() - self.height()))
        self.move(frmX, frmY)
        
        self.show()
        self.raise_()
        self.activateWindow()

        if get_image_save_enabled():
            self._save_image(pixmap)

    def _save_image(self, pixmap) -> None:
        save_dir = get_image_save_dir()
        if not save_dir:
            return
        try:
            out_dir = Path(save_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            base = os.path.basename(self._url.split("?")[0]).strip()
            if not base or "." not in base:
                h = hashlib.sha1(self._url.encode("utf-8")).hexdigest()[:10]
                base = f"image_{int(time.time())}_{h}.jpg"
            out_path = out_dir / base
            if out_path.exists():
                stem = out_path.stem
                suf = out_path.suffix or ".jpg"
                for i in range(1, 1000):
                    cand = out_dir / f"{stem}_{i}{suf}"
                    if not cand.exists():
                        out_path = cand
                        break
            pixmap.save(str(out_path))
        except Exception:
            pass


def show_image(url: str) -> None:
    if url is None or url.strip() == "":
        return

    dlg = ImagePopup(url.strip())

    _ACTIVE_DIALOGS.append(dlg)

    def _forget():
        if dlg in _ACTIVE_DIALOGS:
            _ACTIVE_DIALOGS.remove(dlg)

    dlg.destroyed.connect(_forget)

def close_all_images() -> None:
    for dlg in list(_ACTIVE_DIALOGS):
        try:
            dlg.close()
        except Exception:
            pass
