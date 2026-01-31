from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QLabel
from PySide6.QtGui import QPixmap, QScreen, QIcon
from ui_settings import get_popup_screens
import sys
import os
from PIL import Image
from PIL import ImageQt
import requests
import random
from io import BytesIO

from pathlib import Path

def resource_path(relative: str) -> str:
    if hasattr(sys, "_MEIPASS"):
        return str(Path(sys._MEIPASS) / relative)
    return str(Path(__file__).parent / relative)


_ACTIVE_DIALOGS: list[QDialog] = []

ICON_PATH = resource_path("MommyIcon.ico")

class ImagePopup(QDialog):
    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.initUI(url)

    def show_on_top(self) -> None:
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
        self.setWindowFlags(Qt.FramelessWindowHint |
                            Qt.WindowStaysOnTopHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground)
        if os.path.exists(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))
        self.label = QLabel(self)
        self.pixmap = QPixmap()
        self.scaled = QPixmap()
        self.getAndSetImageFromURL(url)
        screens = QApplication.screens()
        allowed = get_popup_screens()
        if allowed is None or not allowed:
            screm = random.choice(screens)
        else:
            idx = random.choice([i for i in allowed if 0 <= i < len(screens)]) if any(0 <= i < len(screens) for i in allowed) else 0
            screm = screens[idx]

        geom = QScreen.availableGeometry(screm)  
        max_w = int(geom.width() * 0.6)
        max_h = int(geom.height() * 0.6)
        
        scaled = self.pixmap.scaled(
            max_w,
            max_h,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        
        self.label.setPixmap(scaled)

        self.resize(scaled.width(), scaled.height())
        
        frmX = geom.x() + random.randint(0, max(0, geom.width() - self.width()))
        frmY = geom.y() + random.randint(0, max(0, geom.height() - self.height()))
        self.move(frmX, frmY)

    def getAndSetImageFromURL(self, imageURL):
        try:
            self.imdata = requests.get(imageURL, timeout=5)
        except requests.RequestException:
            return
        self.max = 800  # max image size
        if self.imdata.status_code == 200:  # if the image was got successfully
            self.imaged = Image.open(BytesIO(self.imdata.content))
            try:
                alpha = self.imaged.getchannel('A')  # get the alpha channel
                bbox = alpha.getbbox()
                # crop as much as possible that's just transparency
                self.cropim = self.imaged.crop(bbox)
            except ValueError:
                self.cropim = self.imaged
            self.cropim.thumbnail((self.max, self.max),
                                  Image.Resampling.LANCZOS)
            self.pixmap = ImageQt.toqpixmap(self.cropim)
        self.label.setPixmap(self.pixmap)


def show_image(url: str) -> None:
    if url is None or url.strip() == "":
        return

    dlg = ImagePopup(url.strip())

    _ACTIVE_DIALOGS.append(dlg)

    def _forget():
        if dlg in _ACTIVE_DIALOGS:
            _ACTIVE_DIALOGS.remove(dlg)

    dlg.destroyed.connect(_forget)

    dlg.show_on_top()

def close_all_images() -> None:
    for dlg in list(_ACTIVE_DIALOGS):
        try:
            dlg.close()
        except Exception:
            pass
