from __future__ import annotations
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout
from PySide6.QtGui import QFont
from PySide6.QtCore import Qt

def run_session_warning_dialog(*, mode: str, title: str, summary: str, intensity, tags: list[str], blocks: list[str]) -> bool:
    """
    Returns True if user accepts (Start), False otherwise.
    mode:
      - "full": show details
      - "minimal": just a short prompt
    """
    dlg = QDialog(None)
    dlg.setWindowTitle("Incoming session")
    dlg.setWindowFlag(Qt.WindowStaysOnTopHint, True)

    layout = QVBoxLayout(dlg)

    headline = QLabel("An incoming session is ready to start.")
    f = QFont()
    f.setPointSize(11)
    f.setBold(True)
    headline.setFont(f)
    layout.addWidget(headline)

    if mode == "full":
        t = (title or "").strip() or "Untitled session"
        layout.addWidget(QLabel(f"<b>Title:</b> {t}"))

        if summary:
            layout.addWidget(QLabel(f"<b>Summary:</b> {summary}"))

        if intensity is not None and str(intensity) != "":
            layout.addWidget(QLabel(f"<b>Max intensity:</b> {intensity}"))

        if tags:
            safe_tags = ", ".join(tags)
            layout.addWidget(QLabel(f"<b>Tags:</b> {safe_tags}"))

        if blocks:
            layout.addWidget(QLabel("<b>Blocks:</b>"))
            layout.addWidget(QLabel("\n".join(f"• {b}" for b in blocks)))

    btn_row = QHBoxLayout()
    btn_start = QPushButton("Start")
    btn_cancel = QPushButton("Decline")

    btn_start.clicked.connect(dlg.accept)
    btn_cancel.clicked.connect(dlg.reject)

    btn_row.addWidget(btn_start)
    btn_row.addWidget(btn_cancel)
    layout.addLayout(btn_row)

    return dlg.exec() == QDialog.Accepted