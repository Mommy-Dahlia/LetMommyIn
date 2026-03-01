# session_compiler.py
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import TheFactory 

import sys

def resource_path(relative: str) -> Path:
    """
    Resolve bundled asset path for dev + PyInstaller onefile.
    """
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / relative
    return Path(__file__).parent / relative

@dataclass(frozen=True)
class CompiledSession:
    name: str
    steps: list[dict]
    chosen_blocks: list[str]   # for logging / debugging

class SessionCompiler:
    def __init__(self, *, roots: list[Path]):
        """
        base_dir should contain:
          - sessions/
          - blocks/
        """
        self.roots = roots
        self.local_root = roots[0]
        self.sessions_dir = self.local_root / "sessions"
        self.blocks_dir = self.local_root / "blocks"
        self.images_csv_path = resource_path("images.csv")

    def _find_first(self, rel: str) -> Path | None:
        for r in self.roots:
            p = r / rel
            if p.exists():
                return p
        return None

    def _load_block_lines(self, block_name: str) -> list[str]:
        p = self._find_first(f"blocks/{block_name}.txt")
        if not p:
            raise FileNotFoundError(f"Missing block: blocks/{block_name}.txt (searched {self.roots})")
        return p.read_text(encoding="utf-8").splitlines()

    def _choose_blocks(self, names: list[str], *, min_n: int, max_n: int, rng: random.Random) -> list[str]:
        if min_n < 0 or max_n < min_n:
            raise ValueError("Invalid choose min/max")
        if max_n > len(names):
            max_n = len(names)
        n = rng.randint(min_n, max_n)
        return rng.sample(names, n)
    
    def compile_script_from_session_json(self, session_path: Path) -> tuple[str, list[str]]:
        """
        Returns:
            combined_script_text,
            chosen_blocks,
            image_csv (may be None)
        """
        data = json.loads(session_path.read_text(encoding="utf-8"))
        name = str(data.get("name") or session_path.stem)
        plan = data.get("plan")
        if not isinstance(plan, list):
            raise ValueError("Session JSON must have a list field: plan")

        seed = data.get("seed", None)
        rng = random.Random(seed)
        
        chosen: list[str] = []
        out_lines: list[str] = []
        
        for item in plan:
            if not isinstance(item, dict):
                raise ValueError("Each plan item must be an object")
            
            if "include" in item:
                block = str(item["include"])
                chosen.append(block)
                out_lines.extend(self._load_block_lines(block))
                continue
            
            if "lines" in item:
                lines = item["lines"]
                if not isinstance(lines, list):
                    raise ValueError("lines must be a list of strings")
                for ln in lines:
                    if not isinstance(ln, str):
                        raise ValueError("lines entries must be strings")
                    out_lines.append(ln)
                continue

            if "choose" in item:
                spec = item["choose"]
                if not isinstance(spec, dict):
                    raise ValueError("choose must be an object")

                names = spec.get("from")
                if not isinstance(names, list) or not names:
                    raise ValueError("choose.from must be a non-empty list")

                min_n = int(spec.get("min", 1))
                max_n = int(spec.get("max", min_n))

                picks = self._choose_blocks([str(x) for x in names], min_n=min_n, max_n=max_n, rng=rng)
                chosen.extend(picks)
                for b in picks:
                    out_lines.extend(self._load_block_lines(b))
                continue

            raise ValueError(f"Unknown plan item keys: {list(item.keys())}")

        return "\n".join(out_lines), chosen

    def compile_steps(self, session_path: Path) -> CompiledSession:
        script_text, chosen_blocks = self.compile_script_from_session_json(session_path)

        raw_lines = script_text.splitlines()

        # 1) per-line delays (#N), remove the suffix
        lines, delays = TheFactory.extract_delays(raw_lines)

        # 2) #PIC replacement (always use bundled images.csv if present)
        csv_path = self._find_first("images.csv")
        if csv_path:
            images = TheFactory.load_images(str(csv_path))
            lines = TheFactory.assign_images(lines, images)

        # 3) wrap to commands
        steps = TheFactory.wrap_output(lines, delays)

        # 4) post-processing (defaults + scoping)
        TheFactory.ensure_timer_s_everywhere(steps)
        steps = TheFactory.apply_effect_scoping(steps)

        name = session_path.stem
        return CompiledSession(name=name, steps=steps, chosen_blocks=chosen_blocks)
    
    def compile_script(self, session_path: Path) -> str:
        """
        Compile a session JSON into the raw script text (blocks pasted together).
        Used for preview.
        """
        script_text, _chosen_blocks = self.compile_script_from_session_json(session_path)
        return script_text
