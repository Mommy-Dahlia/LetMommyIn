# context.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import queue

from session_runner import SessionRunner
from audio_manager import AudioManager
from subliminal_manager import SubliminalManager
from wfm_manager import WfmManager

@dataclass
class CommandContext:
    session_runner: Optional[SessionRunner] = None
    audio_manager: Optional[AudioManager] = None
    subliminal_manager: Optional[SubliminalManager] = None
    wfm_manager: Optional[WfmManager] = None
    ack_queue: Optional["queue.Queue[dict]"] = None
