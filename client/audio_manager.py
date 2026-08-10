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
        
        self._media_devices = QMediaDevices(self)
        self._media_devices.audioOutputsChanged.connect(self.on_audio_devices_changed)

        self._stop_timer: Optional[QTimer] = None
        self.state = AudioState()

    def play(self, *, url: str, volume: float = 0.8, loop: bool = True, duration_s: float | None = None) -> None:
        url = (url or "").strip()
        if not url:
            raise ValueError("audio_play missing url")

        # stop any existing audio first
        self.reset()

        self._audio.setVolume(max(0.0, min(1.0, float(volume))))
        self._player.setSource(QUrl(url))

        if loop:
            try:
                self._player.setLoops(QMediaPlayer.Infinite)
            except Exception:
                pass
    
        # Start playback immediately QT should handle asnyc loading the source internally
        self._player.play()
        
        self.state.url = url
        self.state.is_playing = True
        
        if duration_s is not None:
            self.start_new_timer(duration_s)

    def stop(self) -> None:
        self.cancel_stop_timer()
        self.reset()
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
            
    def on_audio_devices_changed(self):
        old_volume = self._audio.volume()

        self._audio = QAudioOutput()
        self._audio.setVolume(old_volume)
        self._player.setAudioOutput(self._audio)
        
    def reset(self):
        self._player.stop()

        # Clears media pipeline IMPORTANT
        self._player.setSource(QUrl())
        
    def start_new_timer(self, duration):
        self.cancel_stop_timer()
        
        self._stop_timer = QTimer(self)
        self._stop_timer.setSingleShot(True)
        self._stop_timer.timeout.connect(self.stop)
        self._stop_timer.start(int(float(duration) * 1000))
        
    def cancel_stop_timer(self):
        if self._stop_timer is not None:
            self._stop_timer.stop()
            self._stop_timer.deleteLater()
            self._stop_timer = None
