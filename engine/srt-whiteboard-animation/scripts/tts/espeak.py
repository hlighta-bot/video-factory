"""Offline Mandarin TTS provider backed by eSpeak-NG and FFmpeg."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .base import TTSProvider


class EspeakTTSProvider(TTSProvider):
    """Create Mandarin speech without network access; voice quality is synthetic."""

    backend_name = "espeak"

    def __init__(
        self,
        voice: str = "zh-CN-XiaoxiaoNeural",
        rate: str = "-10%",
        pitch: str = "-1Hz",
    ):
        # eSpeak-NG's `cmn` voice is the Latin-as-English Mandarin variant;
        # `zh` is its Chinese voice and should receive Chinese text directly.
        self.voice = "zh" if voice.startswith("zh-") else voice
        self.rate = rate
        self.pitch = pitch

    def synthesize(self, text: str, output_path: Path) -> Path:
        if not text.strip():
            raise ValueError("TTS text must not be empty")

        espeak = shutil.which("espeak-ng") or shutil.which("espeak")
        ffmpeg = shutil.which("ffmpeg")
        if not espeak:
            raise RuntimeError("Offline TTS requires espeak-ng or espeak on PATH")
        if not ffmpeg:
            raise RuntimeError("Offline TTS requires ffmpeg on PATH to encode MP3")

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        speed = self._speed_wpm(self.rate)

        with tempfile.TemporaryDirectory(
            prefix="video-factory-tts-", dir=str(output_path.parent)
        ) as temp_dir:
            wav_path = Path(temp_dir) / "speech.wav"
            mp3_path = Path(temp_dir) / "speech.mp3"
            speech = subprocess.run(
                [espeak, "-v", self.voice, "-s", str(speed), "-w", str(wav_path), text],
                capture_output=True,
                text=True,
            )
            if speech.returncode != 0 or not wav_path.exists():
                detail = speech.stderr.strip() or speech.stdout.strip()
                raise RuntimeError(f"eSpeak-NG synthesis failed: {detail}")

            encode = subprocess.run(
                [
                    ffmpeg, "-nostdin", "-y", "-v", "error", "-i", str(wav_path),
                    "-codec:a", "libmp3lame", "-q:a", "4", str(mp3_path),
                ],
                capture_output=True,
                text=True,
            )
            if encode.returncode != 0 or not mp3_path.exists():
                detail = encode.stderr.strip() or encode.stdout.strip()
                raise RuntimeError(f"FFmpeg MP3 encoding failed: {detail}")

            mp3_path.replace(output_path)

        return output_path

    def _speed_wpm(self, rate: str) -> int:
        match = re.fullmatch(r"\s*([+-]?\d+(?:\.\d+)?)%\s*", rate)
        if not match:
            raise ValueError(f"Unsupported eSpeak rate format: {rate!r}; use a percentage")
        percent = float(match.group(1))
        # Keep offline Mandarin narration at a measured pace. The SRT timeline
        # follows the measured audio duration, so do not speed up speech just
        # to fit the original cue windows; that made the previous 315 WPM
        # setting difficult to understand.
        base_wpm = 200 if self.voice.startswith(("cmn", "zh")) else 175
        return max(80, min(450, round(base_wpm * (100 + percent) / 100)))
