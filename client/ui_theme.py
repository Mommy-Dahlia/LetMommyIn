from __future__ import annotations
import os
import sys
from pathlib import Path
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

def resource_path(relative: str) -> str:
    if hasattr(sys, "_MEIPASS"):
        return str(Path(sys._MEIPASS) / relative)
    return str(Path(__file__).parent / relative)

# Lifted from your MessagePopup colors
THEME_QSS = """
/* ---- Global base ---- */
QWidget {
    background-color: #e8d9f1;
    color: #4b006e;
    font-size: 12px;
}
QDialog {
    background-color: #e8d9f1;
}

/* ---- Buttons ---- */
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

/* ---- Inputs ---- */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background-color: #ffffff;
    color: #4b006e;
    border: 2px solid #dcc6ea;
    padding: 6px;
    border-radius: 8px;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border: 2px solid #4b006e;
}

/* ---- Lists ---- */
QListWidget, QTreeWidget, QTableWidget {
    background-color: #ffffff;
    border: 2px solid #dcc6ea;
    border-radius: 8px;
}
QListWidget::item:selected {
    background-color: #dcc6ea;
    color: #4b006e;
}

/* ---- Menus (tray + context menus) ---- */
QMenu {
    background-color: #e8d9f1;
    color: #4b006e;
    border: 1px solid #4b006e;
}
QMenu::item {
    padding: 6px 18px;
}
QMenu::item:selected {
    background-color: #dcc6ea;
}
"""

def apply_app_theme(app: QApplication) -> None:
    app.setStyleSheet(THEME_QSS)

    icon_path = resource_path("MommyIcon.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))