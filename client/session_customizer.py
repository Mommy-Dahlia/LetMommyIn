# session_customizer.py
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QPushButton,
    QLineEdit, QGroupBox, QFormLayout, QCheckBox, QMessageBox, QAbstractItemView, QTextEdit
)

_SAFE_NAME = re.compile(r"[^a-zA-Z0-9_\- ]+")

def _safe_stem(name: str) -> str:
    name = (name or "").strip()
    name = _SAFE_NAME.sub("", name).strip()
    name = name.replace(" ", "_")
    return name or "custom_session"

def list_block_names(content_roots: list[Path]) -> list[str]:
    """
    Return unique block stems across all roots, preferring earlier roots.
    Mirrors your session listing approach that dedupes by stem :contentReference[oaicite:8]{index=8}.
    """
    seen = set()
    out: list[str] = []
    for root in content_roots:
        bdir = root / "blocks"
        for p in sorted(bdir.glob("*.txt")):
            stem = p.stem
            if stem in seen:
                continue
            seen.add(stem)
            out.append(stem)
    return out

def find_block_file(content_roots: list[Path], block_stem: str) -> Path | None:
    """
    Search roots in order (local first, then bundled) and return the first matching blocks/<stem>.txt.
    """
    for root in content_roots:
        cand = root / "blocks" / f"{block_stem}.txt"
        if cand.exists():
            return cand
    return None


def find_block_meta_file(content_roots: list[Path], block_stem: str) -> Path | None:
    """
    Search roots in order and return the first matching blocks/<stem>.meta.json.
    """
    for root in content_roots:
        cand = root / "blocks" / f"{block_stem}.meta.json"
        if cand.exists():
            return cand
    return None

def load_block_meta(content_roots: list[Path], block_stem: str) -> dict:
    """
    Loads {title, summary, tags, intensity} from blocks/<stem>.meta.json if present.
    Returns normalized defaults if missing or invalid.
    """
    meta_path = find_block_meta_file(content_roots, block_stem)
    if meta_path is None:
        return {
            "title": block_stem,
            "summary": "",
            "tags": [],
            "intensity": None,
        }

    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        # If meta is malformed, fail soft (don't break UI)
        return {
            "title": block_stem,
            "summary": "",
            "tags": [],
            "intensity": None,
        }

    title = str(data.get("title") or block_stem)
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

    return {
        "title": title,
        "summary": summary,
        "tags": tags,
        "intensity": intensity,
    }

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
class CustomizerResult:
    session_path: Path

class SessionCustomizerDialog(QDialog):
    def __init__(self, *, content_roots: list[Path], sessions_dir: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Session Customizer")

        self._content_roots = content_roots
        self._sessions_dir = sessions_dir

        root = QVBoxLayout(self)

        # ---- Name ----
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Session name (used as filename)")
        root.addWidget(QLabel("Session name:"))
        root.addWidget(self.name_edit)

        # ---- Block pickers ----
        row = QHBoxLayout()
        root.addLayout(row)

        # 1) metadata sidecar (left)
        meta_col = QVBoxLayout()
        row.addLayout(meta_col, 1)

        meta_col.addWidget(QLabel("Block info:"))
        self.meta_title = QLabel("—")
        self.meta_title.setWordWrap(True)

        self.meta_summary = QLabel("")
        self.meta_summary.setWordWrap(True)

        self.meta_tags = QLabel("")
        self.meta_tags.setWordWrap(True)

        self.meta_intensity = QLabel("")

        meta_col.addWidget(self.meta_title)
        meta_col.addWidget(self.meta_summary)
        meta_col.addWidget(self.meta_tags)
        meta_col.addWidget(self.meta_intensity)
        meta_col.addStretch(1)

        # 2) available blocks (middle)
        left = QVBoxLayout()
        row.addLayout(left, 1)

        # 3) selected blocks (right)
        right = QVBoxLayout()
        row.addLayout(right, 1)

        left.addWidget(QLabel("Available blocks:"))
        self.available = QListWidget()
        self.available.setSelectionMode(QAbstractItemView.ExtendedSelection)
        left.addWidget(self.available)

        btns = QHBoxLayout()
        self.btn_add = QPushButton("Add →")
        self.btn_remove = QPushButton("← Remove")
        btns.addWidget(self.btn_add)
        btns.addWidget(self.btn_remove)
        left.addLayout(btns)

        right.addWidget(QLabel("Selected blocks (order matters):"))
        self.selected = QListWidget()
        self.selected.setSelectionMode(QAbstractItemView.ExtendedSelection)
        right.addWidget(self.selected)

        order_btns = QHBoxLayout()
        self.btn_up = QPushButton("↑ Up")
        self.btn_down = QPushButton("↓ Down")
        self.available.currentItemChanged.connect(self._on_available_changed)
        order_btns.addWidget(self.btn_up)
        order_btns.addWidget(self.btn_down)
        right.addLayout(order_btns)

        # ---- Options ----
        opts = QGroupBox("Options")
        form = QFormLayout(opts)

        self.chk_audio = QCheckBox("Add audio")
        self.audio_url = QLineEdit()
        self.audio_url.setPlaceholderText("URL (optional). Leave blank to rely on script defaults if supported.")
        form.addRow(self.chk_audio, self.audio_url)

        self.chk_overlay = QCheckBox("Add gif overlay")
        self.overlay_url = QLineEdit()
        self.overlay_url.setPlaceholderText("GIF URL (optional)")
        form.addRow(self.chk_overlay, self.overlay_url)

        self.chk_sub = QCheckBox("Add subliminals")
        self.sub_tags = QLineEdit()
        self.sub_tags.setPlaceholderText("tags, comma-separated (e.g. premelt, lmi)")
        form.addRow(self.chk_sub, self.sub_tags)

        root.addWidget(opts)

        # ---- Save / Cancel ----
        bottom = QHBoxLayout()
        self.btn_preview = QPushButton("Preview script")
        self.btn_save = QPushButton("Save")
        self.btn_cancel = QPushButton("Cancel")
        bottom.addWidget(self.btn_preview)
        bottom.addStretch(1)
        bottom.addWidget(self.btn_save)
        bottom.addWidget(self.btn_cancel)
        root.addLayout(bottom)

        # wiring
        self.btn_add.clicked.connect(self._add_blocks)
        self.btn_remove.clicked.connect(self._remove_blocks)
        self.btn_up.clicked.connect(lambda: self._move(-1))
        self.btn_down.clicked.connect(lambda: self._move(1))
        self.btn_preview.clicked.connect(self._preview)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_save.clicked.connect(self._save)

        # populate available blocks
        for b in list_block_names(self._content_roots):
            self.available.addItem(b)
            
        if self.available.count() > 0:
            self.available.setCurrentRow(0)

        self.result: CustomizerResult | None = None

    def _add_blocks(self):
        for item in self.available.selectedItems():
            self.selected.addItem(item.text())

    def _remove_blocks(self):
        for item in list(self.selected.selectedItems()):
            row = self.selected.row(item)
            self.selected.takeItem(row)
            
    def _preview(self) -> None:
        if self.selected.count() == 0:
            QMessageBox.information(self, "Nothing to preview", "Select at least one block first.")
            return

        parts: list[str] = []

        # If you want the preview to reflect options, include the directives.
        pre_lines: list[str] = []
        post_lines: list[str] = []

        if self.chk_audio.isChecked():
            url = self.audio_url.text().strip()
            pre_lines.append(f"#AUDIO - {url}" if url else "#AUDIO")
            post_lines.append("#AUDIOSTOP")

        if self.chk_overlay.isChecked():
            url = self.overlay_url.text().strip()
            pre_lines.append(f"#GIF - {url}" if url else "#GIF")
            post_lines.append("#GIFSTOP")

        if self.chk_sub.isChecked():
            tags = self.sub_tags.text().strip()
            if tags:
                pre_lines.append(f"#SUB - {tags}")

        if pre_lines:
            parts.append("\n".join(pre_lines))

        # Concatenate block texts in selected order
        for i in range(self.selected.count()):
            stem = self.selected.item(i).text()
            p = find_block_file(self._content_roots, stem)
            if p is None:
                parts.append(f"[Missing block: {stem}]")
                continue
            try:
                parts.append(p.read_text(encoding="utf-8"))
            except Exception as e:
                parts.append(f"[Failed to read block {stem}: {e}]")

        if post_lines:
            parts.append("\n".join(post_lines))

        preview_text = "\n\n".join(parts).strip() + "\n"
        dlg = PreviewDialog(preview_text, parent=self)
        dlg.exec()
    
    def _render_meta(self, block_stem: str) -> None:
        meta = load_block_meta(self._content_roots, block_stem)

        self.meta_title.setText(f"<b>{meta['title']}</b>")
        self.meta_summary.setText(meta["summary"] or "")

        tags = meta["tags"] or []
        self.meta_tags.setText(f"<b>Tags:</b> {', '.join(tags) if tags else '—'}")

        intensity = meta["intensity"]
        self.meta_intensity.setText(f"<b>Intensity:</b> {intensity if intensity is not None else '—'}")


    def _on_available_changed(self, current, previous) -> None:
        # Fires when the user clicks a different item or uses keyboard navigation.
        if current is None:
            # Clear panel when nothing selected
            self.meta_title.setText("—")
            self.meta_summary.setText("")
            self.meta_tags.setText("")
            self.meta_intensity.setText("")
            return

        self._render_meta(current.text())

    def _move(self, delta: int):
        row = self.selected.currentRow()
        if row < 0:
            return
        new_row = row + delta
        if new_row < 0 or new_row >= self.selected.count():
            return
        item = self.selected.takeItem(row)
        self.selected.insertItem(new_row, item)
        self.selected.setCurrentRow(new_row)

    def _save(self):
        # Validate
        if self.selected.count() == 0:
            QMessageBox.warning(self, "Missing blocks", "Select at least one block.")
            return

        name = self.name_edit.text().strip()
        stem = _safe_stem(name)
        out_path = self._sessions_dir / f"{stem}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Build plan using the new {"lines": [...]} feature plus {"include": ...}
        pre_lines: list[str] = []
        post_lines: list[str] = []

        if self.chk_audio.isChecked():
            url = self.audio_url.text().strip()
            pre_lines.append(f"#AUDIO - {url}" if url else "#AUDIO")
            post_lines.append("#AUDIOSTOP")

        if self.chk_overlay.isChecked():
            url = self.overlay_url.text().strip()
            pre_lines.append(f"#GIF - {url}" if url else "#GIF")
            post_lines.append("#GIFSTOP")

        if self.chk_sub.isChecked():
            tags = self.sub_tags.text().strip()
            if tags:
                pre_lines.append(f"#SUB - {tags}")
            else:
                QMessageBox.warning(self, "Missing tags", "Enter subliminal tags (comma-separated), or uncheck subliminals.")
                return

        plan: list[dict] = []
        if pre_lines:
            plan.append({"lines": pre_lines})

        for i in range(self.selected.count()):
            plan.append({"include": self.selected.item(i).text()})

        if post_lines:
            plan.append({"lines": post_lines})

        payload = {
            "name": name or stem,
            "plan": plan,
        }

        try:
            out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as e:
            QMessageBox.critical(self, "Save failed", f"Could not write:\n{out_path}\n\n{e}")
            return

        self.result = CustomizerResult(session_path=out_path)
        self.accept()