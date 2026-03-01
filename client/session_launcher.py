from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QPushButton,
    QMessageBox, QAbstractItemView, QTextEdit
)


def find_session_file(content_roots: list[Path], session_stem: str) -> Path | None:
    for root in content_roots:
        p = root / "sessions" / f"{session_stem}.json"
        if p.exists():
            return p
    return None


def find_session_meta_file(content_roots: list[Path], session_stem: str) -> Path | None:
    for root in content_roots:
        p = root / "sessions" / f"{session_stem}.meta.json"
        if p.exists():
            return p
    return None


def load_session_meta(content_roots: list[Path], session_stem: str) -> dict:
    meta_path = find_session_meta_file(content_roots, session_stem)
    if meta_path is None:
        return {"title": session_stem, "summary": "", "tags": [], "intensity": None}

    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {"title": session_stem, "summary": "", "tags": [], "intensity": None}

    title = str(data.get("title") or session_stem)
    summary = str(data.get("summary") or "")
    tags = data.get("tags") or []
    if not isinstance(tags, list):
        tags = []
    tags = [str(t) for t in tags if str(t).strip()]

    intensity = data.get("intensity", None)
    try:
        intensity = int(intensity) if intensity is not None else None
    except Exception:
        intensity = None

    return {"title": title, "summary": summary, "tags": tags, "intensity": intensity}

def list_session_stems(content_roots: list[Path]) -> list[str]:
    seen = set()
    out: list[str] = []
    for root in content_roots:
        sdir = root / "sessions"
        for p in sorted(sdir.glob("*.json")):
            if p.name.endswith(".meta.json"):
                continue
            stem = p.stem
            if stem in seen:
                continue
            seen.add(stem)
            out.append(stem)
    return out

class PreviewDialog(QDialog):
    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Session Preview")
        root = QVBoxLayout(self)

        box = QTextEdit()
        box.setReadOnly(True)
        box.setPlainText(text)
        root.addWidget(box)

        btn = QPushButton("Close")
        btn.clicked.connect(self.accept)
        root.addWidget(btn)
        
@dataclass
class SessionLauncherResult:
    session_stem: str
    session_path: Path


class SessionLauncherDialog(QDialog):
    def __init__(self, *, content_roots: list[Path], compiler, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sessions")

        self._content_roots = content_roots
        self._compiler = compiler  # your SessionCompiler instance

        root = QVBoxLayout(self)

        row = QHBoxLayout()
        root.addLayout(row)

        # sidecar metadata (left)
        meta_col = QVBoxLayout()
        row.addLayout(meta_col, 1)

        meta_col.addWidget(QLabel("Session info:"))
        self.meta_title = QLabel("—")
        self.meta_title.setWordWrap(True)

        self.meta_summary = QLabel("")
        self.meta_summary.setWordWrap(True)

        self.meta_tags = QLabel("")
        self.meta_tags.setWordWrap(True)

        self.meta_intensity = QLabel("")
        self.meta_intensity.setWordWrap(True)

        meta_col.addWidget(self.meta_title)
        meta_col.addWidget(self.meta_summary)
        meta_col.addWidget(self.meta_tags)
        meta_col.addWidget(self.meta_intensity)
        meta_col.addStretch(1)

        # sessions list (middle)
        list_col = QVBoxLayout()
        row.addLayout(list_col, 2)

        list_col.addWidget(QLabel("Available sessions:"))
        self.sessions = QListWidget()
        self.sessions.setSelectionMode(QAbstractItemView.SingleSelection)
        list_col.addWidget(self.sessions)

        # buttons (bottom)
        bottom = QHBoxLayout()
        self.btn_preview = QPushButton("Preview script")
        self.btn_run = QPushButton("Run")
        self.btn_cancel = QPushButton("Cancel")
        bottom.addWidget(self.btn_preview)
        bottom.addStretch(1)
        bottom.addWidget(self.btn_run)
        bottom.addWidget(self.btn_cancel)
        root.addLayout(bottom)

        self.btn_cancel.clicked.connect(self.reject)
        self.btn_run.clicked.connect(self._run)
        self.btn_preview.clicked.connect(self._preview)
        self.sessions.currentItemChanged.connect(self._on_session_changed)

        for stem in list_session_stems(self._content_roots):
            self.sessions.addItem(stem)

        if self.sessions.count() > 0:
            self.sessions.setCurrentRow(0)

        self.result: SessionLauncherResult | None = None

    def _render_meta(self, session_stem: str) -> None:
        meta = load_session_meta(self._content_roots, session_stem)
        self.meta_title.setText(f"<b>{meta['title']}</b>")
        self.meta_summary.setText(meta["summary"] or "")

        tags = meta["tags"] or []
        self.meta_tags.setText(f"<b>Tags:</b> {', '.join(tags) if tags else '—'}")

        intensity = meta["intensity"]
        self.meta_intensity.setText(f"<b>Intensity:</b> {intensity if intensity is not None else '—'}")

    def _on_session_changed(self, current, previous) -> None:
        if current is None:
            self.meta_title.setText("—")
            self.meta_summary.setText("")
            self.meta_tags.setText("")
            self.meta_intensity.setText("")
            return
        self._render_meta(current.text())

    def _get_selected(self) -> tuple[str, Path] | None:
        item = self.sessions.currentItem()
        if item is None:
            return None
        stem = item.text()
        p = find_session_file(self._content_roots, stem)
        if p is None:
            QMessageBox.warning(self, "Missing session", f"Could not find file for: {stem}")
            return None
        return stem, p

    def _preview(self) -> None:
        sel = self._get_selected()
        if not sel:
            return
        stem, path = sel

        # Prefer a "compile script" method (we'll add it below).
        try:
            text = self._compiler.compile_script(path)
        except Exception as e:
            QMessageBox.critical(self, "Preview failed", f"Could not compile preview:\n{e}")
            return

        PreviewDialog(text, parent=self).exec()

    def _run(self) -> None:
        sel = self._get_selected()
        if not sel:
            return
        stem, path = sel
        self.result = SessionLauncherResult(session_stem=stem, session_path=path)
        self.accept()