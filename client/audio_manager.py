# audio_manager.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QObject, QUrl, QTimer
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput, QMediaDevices, QAudioDevice


@dataclass
class AudioState:
    url: str = ""
    is_playing: bool = False


class AudioManager(QObject):
    """
    Lives on the Qt thread. Owns QMediaPlayer + QAudioOutput.
    """
    def __init__(self):
        super().__init__()
        self._audio = QAudioOutput()
        self._player = QMediaPlayer()
        self._player.setAudioOutput(self._audio)

        self._stop_timer: Optional[QTimer] = None
        self.state = AudioState()

    def play(self, *, url: str, volume: float = 0.8, loop: bool = True, duration_s: float | None = None) -> None:
        url = (url or "").strip()
        if not url:
            raise ValueError("audio_play missing url")

        # stop any existing audio first
        self.stop()

        self._audio.setVolume(max(0.0, min(1.0, float(volume))))

        self._player.setSource(QUrl(url))

        if loop:
            # Qt6: QMediaPlayer has setLoops in recent versions.
            # If this attribute doesn't exist in your PySide6 build, we’ll add a fallback.
            if hasattr(self._player, "setLoops"):
                self._player.setLoops(QMediaPlayer.Infinite)
        self._player.play()

        self.state.url = url
        self.state.is_playing = True

        if duration_s is not None:
            self._stop_timer = QTimer(self)
            self._stop_timer.setSingleShot(True)
            self._stop_timer.timeout.connect(self.stop)
            self._stop_timer.start(int(float(duration_s) * 1000))

    def stop(self) -> None:
        if self._stop_timer is not None:
            self._stop_timer.stop()
            self._stop_timer.deleteLater()
            self._stop_timer = None

        self._player.stop()
        self.state = AudioState()
        
    def get_audio_device_choices(self) -> list[tuple[str, str | None]]:
        """
        Returns [(label, device_id_or_none)].
        device_id is a stable-ish identifier we store in config.
        """
        out = [("Default", None)]
        for dev in QMediaDevices.audioOutputs():
            # id() is QByteArray; convert to hex string
            dev_id = bytes(dev.id()).hex()
            out.append((dev.description(), dev_id))
        return out

    def set_output_device_by_id(self, dev_id_hex: str | None) -> None:
        if not dev_id_hex:
            return  # default device
        target = None
        for dev in QMediaDevices.audioOutputs():
            if bytes(dev.id()).hex() == dev_id_hex:
                target = dev
                break
        if target is not None:
            self._audio.setDevice(target)
