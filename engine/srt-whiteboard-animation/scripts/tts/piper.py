"""Piper TTS provider: fully offline English (or Chinese) narration.

Why it exists:
    Edge TTS gives the most natural English narrator, but it needs a network
    round trip. Piper is local, fast (RTF ~0.25 on CPU) and already installed on
    this machine, so it can serve as a deterministic offline fallback for the
    ``en-*-local-narrator`` voice presets.

Resolution order for the model:
    1. ``voice`` given as an absolute ``.onnx`` path -> used as is.
    2. ``PIPER_VOICES_DIR`` (default ``/home/hqb/ai/models/tts/piper/voices``)
       searched recursively for ``<voice>.onnx`` (e.g. ``en_US-ryan-high``).

Other environment knobs:
    ``PIPER_BINARY``   default ``/home/hqb/ai-apps/piper/build/piper``
    ``PIPER_LIB_DIR``  default ``/home/hqb/ai-apps/piper/build/pi/lib``
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .base import TTSProvider
from .voice_profiles import rate_to_length_scale

DEFAULT_PIPER_BINARY = Path("/home/hqb/ai-apps/piper/build/piper")
DEFAULT_PIPER_LIB_DIR = Path("/home/hqb/ai-apps/piper/build/pi/lib")
DEFAULT_VOICES_DIR = Path("/home/hqb/ai/models/tts/piper/voices")


class PiperTTSProvider(TTSProvider):
    """Local neural TTS through the Piper CLI (WAV out, MP3 for the pipeline)."""

    backend_name = "piper"

    def __init__(
        self,
        voice: str = "en_US-ryan-high",
        rate: str = "+0%",
        pitch: str = "+0Hz",
        *,
        binary: str | Path | None = None,
        lib_dir: str | Path | None = None,
        voices_dir: str | Path | None = None,
        sentence_silence: float = 0.2,
    ):
        self.voice = voice
        self.rate = rate
        self.pitch = pitch
        self.length_scale = rate_to_length_scale(rate)
        self.binary = Path(binary or os.getenv("PIPER_BINARY") or DEFAULT_PIPER_BINARY)
        self.lib_dir = Path(lib_dir or os.getenv("PIPER_LIB_DIR") or DEFAULT_PIPER_LIB_DIR)
        self.voices_dir = Path(voices_dir or os.getenv("PIPER_VOICES_DIR") or DEFAULT_VOICES_DIR)
        self.sentence_silence = float(sentence_silence)
        self.warnings: list[str] = []
        if pitch and pitch not in {"0", "0Hz", "+0Hz", "-0Hz"}:
            self.warnings.append("Piper does not support pitch; pitch request ignored")

    # ------------------------------------------------------------------ helpers
    def resolve_model_path(self) -> Path:
        """Locate the Piper ``.onnx`` model for the configured voice."""
        candidate = Path(self.voice)
        if candidate.suffix == ".onnx" and candidate.exists():
            return candidate
        needle = candidate.name if candidate.name else str(candidate)
        if needle.endswith(".onnx"):
            needle = needle[: -len(".onnx")]
        if self.voices_dir.exists():
            for path in self.voices_dir.rglob(needle + ".onnx"):
                return path
        raise FileNotFoundError(
            "Piper voice %r not found under %s. Available examples: %s"
            % (needle, self.voices_dir, self._sample_voices())
        )

    def _sample_voices(self, limit: int = 8) -> str:
        if not self.voices_dir.exists():
            return "(voices dir missing)"
        found = sorted(p.stem for p in self.voices_dir.rglob("*.onnx"))[:limit]
        return ", ".join(found) if found else "(none)"

    def _environment(self) -> dict:
        env = dict(os.environ)
        lib = str(self.lib_dir)
        if self.lib_dir.exists():
            env["LD_LIBRARY_PATH"] = lib + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
        return env

    def _to_mp3(self, wav_path: Path, output_path: Path) -> Path:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            # Fall back to copying the WAV but keep the caller informed.
            shutil.copyfile(wav_path, output_path.with_suffix(".wav"))
            self.warnings.append(
                "ffmpeg missing: narration kept as WAV (%s)" % output_path.with_suffix(".wav")
            )
            return output_path.with_suffix(".wav")
        result = subprocess.run(
            [
                ffmpeg, "-nostdin", "-y", "-v", "error",
                "-i", str(wav_path),
                "-codec:a", "libmp3lame", "-b:a", "160k",
                str(output_path),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or not output_path.exists():
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError("ffmpeg failed to encode Piper output: %s" % detail)
        return output_path

    # ---------------------------------------------------------------- interface
    def synthesize(self, text: str, output_path: Path) -> Path:
        if not text.strip():
            raise ValueError("TTS text must not be empty")

        model_path = self.resolve_model_path()
        if not self.binary.exists():
            raise RuntimeError(
                "Piper binary not found at %s (set PIPER_BINARY)" % self.binary
            )

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(
            prefix="video-factory-piper-", dir=str(output_path.parent)
        ) as temp_dir:
            wav_path = Path(temp_dir) / "speech.wav"
            command = [
                str(self.binary),
                "--model", str(model_path),
                "--output_file", str(wav_path),
                "--length_scale", str(self.length_scale),
                "--sentence_silence", str(self.sentence_silence),
                "--quiet",
            ]
            result = subprocess.run(
                command,
                input=text,
                capture_output=True,
                text=True,
                env=self._environment(),
            )
            if result.returncode != 0 or not wav_path.exists():
                detail = (result.stderr or result.stdout or "").strip()
                raise RuntimeError("Piper synthesis failed: %s" % detail)

            if output_path.suffix.lower() == ".mp3":
                return self._to_mp3(wav_path, output_path)
            shutil.copyfile(wav_path, output_path)
            return output_path
