import sys
import os
from PySide6.QtWidgets import QPushButton, QLabel, QDialog, QVBoxLayout, QApplication
from PySide6.QtGui import QFont, QIcon
from PySide6.QtCore import Qt, QTimer, QPoint
from pathlib import Path
from ui_settings import get_popup_screens

def resource_path(relative: str) -> str:
    """
    Resolve resource paths for dev and PyInstaller builds.
    """
    if hasattr(sys, "_MEIPASS"):
        return str(Path(sys._MEIPASS) / relative)
    return str(Path(__file__).parent / relative)


_ACTIVE_DIALOGS: list[QDialog] = []

class MessagePopup(QDialog):
    
    def show_on_top(self):
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.show()
        self.raise_()
        self.activateWindow()

    def __init__(self, title: str | None, body: str, lifespan_s: int | None = None, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowStaysOnTopHint
            | Qt.Window
        )
        self.setWindowFlag(Qt.WindowCloseButtonHint, False)
        self.setWindowFlag(Qt.WindowSystemMenuHint, False)
        self.setStyleSheet("""
            QDialog {
                background-color: #e8d9f1;
            }
            QLabel {
                color: #4b006e;
            }
            QPushButton {
                background-color: #dcc6ea;
                color: #4b006e;
                border: 1px solid #4b006e;
                padding: 6px 10px;
                border-radius: 8px;
            }
            QPushButton:hover {
                background-color: #e8d9f1;
            }
        """)

        font1 = QFont()
        font1.setPointSize(12)
        icon_path = resource_path("MommyIcon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        self.resize(450, 100)
        screens = QApplication.screens()
        allowed = get_popup_screens()
        if allowed:
            screens = [screens[i] for i in allowed if 0 <= i < len(screens)] or screens
        scr = screens[0]
        geom = scr.availableGeometry()
        x = geom.x() + (geom.width() - self.width()) // 2
        y = geom.y() + (geom.height() - self.height()) // 2
        self.move(QPoint(x, y))
        self.setWindowTitle(title or "")
        self.label = QLabel(body or "")
        self.label.setFont(font1)
        self.label.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.button = QPushButton("Yes Mommy")
        self.button.setFont(font1)
        self.button.clicked.connect(self.close)
        layout = QVBoxLayout(self)
        layout.addWidget(self.label)
        layout.addWidget(self.button)
        if lifespan_s is not None: 
            QTimer.singleShot(int(lifespan_s*1000), self.close)

def show_message(title: str | None, body: str, lifespan_s: int | None = None) -> None:
    if body is None or body == "":
        return

    dlg = MessagePopup(title=title, body=body, lifespan_s=lifespan_s)
    
    _ACTIVE_DIALOGS.append(dlg)
    def _forget():
        if dlg in _ACTIVE_DIALOGS:
            _ACTIVE_DIALOGS.remove(dlg)
    dlg.destroyed.connect(_forget)
    
    dlg.show_on_top()

def close_all_messages() -> None:
    for dlg in list(_ACTIVE_DIALOGS):
        try:
            dlg.close()
        except Exception:
            pass
