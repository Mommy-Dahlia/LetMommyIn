from __future__ import annotations
from dataclasses import dataclass
from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout, QPlainTextEdit

@dataclass
class InjectEvent:
    kind: str          # "block" | "session"
    title: str
    overwritten: bool = False

class InjectionBatchNotifier(QObject):
    """
    Collects inject events and shows one summary dialog after a quiet period.
    """
    def __init__(self, *, quiet_ms: int = 800):
        super().__init__()
        self._quiet_ms = quiet_ms
        self._events: list[InjectEvent] = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._show_dialog)

    def add(self, ev: InjectEvent) -> None:
        self._events.append(ev)
        # Restart timer every time we receive something. When traffic stops, we show one dialog.
        self._timer.start(self._quiet_ms)

    def _show_dialog(self) -> None:
        if not self._events:
            return

        blocks = [e for e in self._events if e.kind == "block"]
        sessions = [e for e in self._events if e.kind == "session"]

        lines = []
        if sessions:
            lines.append(f"Sessions received: {len(sessions)}")
            for e in sessions:
                lines.append(f"  • {e.title}" + (" (overwrote)" if e.overwritten else ""))
            lines.append("")
        if blocks:
            lines.append(f"Blocks received: {len(blocks)}")
            for e in blocks[:40]:
                lines.append(f"  • {e.title}" + (" (overwrote)" if e.overwritten else ""))
            if len(blocks) > 40:
                lines.append(f"  …and {len(blocks) - 40} more")

        dlg = QDialog(None)
        dlg.setWindowTitle("Content received")
        dlg.setWindowFlag(Qt.WindowStaysOnTopHint, True)

        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel("Injected content was saved to your local library."))

        box = QPlainTextEdit()
        box.setReadOnly(True)
        box.setPlainText("\n".join(lines).strip())
        layout.addWidget(box)

        btn_row = QHBoxLayout()
        ok = QPushButton("OK")
        ok.clicked.connect(dlg.accept)
        btn_row.addWidget(ok)
        layout.addLayout(btn_row)

        dlg.exec()

        # clear after showing
        self._events.clear()