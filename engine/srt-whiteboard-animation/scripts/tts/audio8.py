"""Audio8 TTS provider routed through the local tts-hub service."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from .base import TTSProvider


DEFAULT_HUB_URL = "http://127.0.0.1:18084"
DEFAULT_AUDIO8_URL = "http://127.0.0.1:8024"
DEFAULT_HUB_SCRIPT = Path("/home/hqb/ai-apps/tts-hub/run_server.sh")
DEFAULT_AUDIO8_SCRIPT = Path("/home/hqb/ai-apps/audio8-tts-onnx-0.1b/serve_8024.sh")
_START_LOCK = threading.Lock()


class Audio8TTSProvider(TTSProvider):
    """Use local Audio8 through tts-hub, which handles chunking and MP3 output."""

    backend_name = "audio8"
    model_name = "Audio8/Audio8-TTS-Preview-0.1B-ONNX-INT8"
    sample_rate = 44100
    channels = 1

    def __init__(
        self,
        voice: str = "default",
        rate: str = "-10%",
        pitch: str = "-1Hz",
        *,
        hub_url: str | None = None,
        audio8_url: str | None = None,
        hub_script: str | Path | None = None,
        audio8_script: str | Path | None = None,
        timeout_s: float = 300.0,
        startup_timeout_s: float = 60.0,
        start_services: bool = True,
    ):
        self.voice = os.getenv("AUDIO8_VOICE", voice or "default")
        self.rate = rate
        self.pitch = pitch
        self.speed = _rate_to_speed(rate)
        self.hub_url = (hub_url or os.getenv("TTS_HUB_URL") or DEFAULT_HUB_URL).rstrip("/")
        self.audio8_url = (
            audio8_url or os.getenv("AUDIO8_SERVICE_URL") or DEFAULT_AUDIO8_URL
        ).rstrip("/")
        self.hub_script = Path(
            hub_script or os.getenv("TTS_HUB_START_SCRIPT") or DEFAULT_HUB_SCRIPT
        )
        self.audio8_script = Path(
            audio8_script or os.getenv("AUDIO8_START_SCRIPT") or DEFAULT_AUDIO8_SCRIPT
        )
        self.timeout_s = float(timeout_s)
        self.startup_timeout_s = float(startup_timeout_s)
        self.start_services = start_services
        self._ready = False
        self._request_count = 0
        self._chunk_count = 0
        self._warnings: list[str] = []
        if pitch and pitch not in {"0", "0Hz", "+0Hz", "-0Hz"}:
            warning = f"Audio8 does not support pitch; ignored requested pitch {pitch}"
            self._warnings.append(warning)
            print(f"[warn] {warning}")

    @property
    def service_url(self) -> str:
        return self.hub_url

    @property
    def request_count(self) -> int:
        return self._request_count

    @property
    def chunk_count(self) -> int:
        return self._chunk_count

    @property
    def warnings(self) -> list[str]:
        return list(self._warnings)

    def ensure_ready(self) -> None:
        if self._ready:
            return
        with _START_LOCK:
            if self._ready:
                return
            if not _probe(self.audio8_url + "/api/health", timeout=3.0):
                self._ensure_process(
                    self.audio8_script,
                    self.audio8_script.parent / "service.log",
                )
                _wait_for_health(
                    self.audio8_url + "/api/health",
                    self.startup_timeout_s,
                    "Audio8 inference service",
                )
            if not _probe(self.hub_url + "/health", timeout=3.0):
                if not self.start_services:
                    raise RuntimeError(
                        f"tts-hub is not ready at {self.hub_url}; start it or enable service autostart"
                    )
                self._ensure_process(
                    self.hub_script,
                    Path(os.getenv("TTS_HUB_LOG", "/tmp/tts-hub.log")),
                )
                _wait_for_health(
                    self.hub_url + "/health",
                    self.startup_timeout_s,
                    "tts-hub",
                )
            self._ready = True

    def _ensure_process(self, script: Path, log_path: Path) -> None:
        if not self.start_services:
            raise RuntimeError(
                f"Audio8 TTS service is not ready; start {script} and retry"
            )
        if not script.is_file():
            raise RuntimeError(
                f"TTS service is not ready and its start script is missing: {script}"
            )
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with log_path.open("ab") as log_file:
                process = subprocess.Popen(
                    ["bash", str(script)],
                    cwd=str(script.parent),
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    close_fds=True,
                )
        except OSError as exc:
            raise RuntimeError(f"Failed to start TTS service with {script}: {exc}") from exc
        # The child daemonizes; give the launcher a brief moment to bind its port.
        time.sleep(0.5)
        if process.poll() not in (None, 0):
            raise RuntimeError(
                f"TTS service launcher exited with code {process.returncode}; see {log_path}"
            )

    def synthesize(self, text: str, output_path: Path) -> Path:
        if not text.strip():
            raise ValueError("TTS text must not be empty")
        self.ensure_ready()
        payload = {
            "input": text,
            "voice": self.voice,
            "response_format": "mp3",
            "speed": self.speed,
            "provider": "audio8",
            "scenario": "chinese_natural",
            "allow_fallback": False,
        }
        request = urllib.request.Request(
            f"{self.hub_url}/v1/audio/speech",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                audio = response.read()
                if len(audio) < 1024:
                    raise RuntimeError(
                        f"Audio8 returned an empty or undersized audio payload ({len(audio)} bytes)"
                    )
                self._request_count += 1
                self._chunk_count += _estimate_chunk_count(text)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Audio8 TTS request failed with HTTP {exc.code}: {detail[:500]}"
            ) from exc
        except urllib.error.URLError as exc:
            self._ready = False
            raise RuntimeError(f"Audio8 TTS service request failed: {exc}") from exc

        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".part")
        temporary.write_bytes(audio)
        temporary.replace(target)
        return target


def _rate_to_speed(rate: str) -> float:
    """Translate the pipeline's signed percentage into tts-hub speed."""
    value = str(rate or "0%").strip()
    if value.endswith("%"):
        try:
            return max(0.5, min(2.0, 1.0 + float(value[:-1]) / 100.0))
        except ValueError as exc:
            raise ValueError(f"Unsupported Audio8 rate value: {rate!r}") from exc
    try:
        speed = float(value)
    except ValueError as exc:
        raise ValueError(f"Unsupported Audio8 rate value: {rate!r}") from exc
    if not 0.5 <= speed <= 2.0:
        raise ValueError("Audio8 speed must be between 0.5 and 2.0")
    return speed


def _estimate_chunk_count(text: str, limit: int = 60) -> int:
    """Estimate hub chunks from its configured maximum characters per part."""
    remaining = len(text.strip())
    chunks = 0
    while remaining > 0:
        chunks += 1
        remaining -= limit
    return max(1, chunks)


def _probe(url: str, timeout: float) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError, urllib.error.HTTPError):
        return False


def _wait_for_health(url: str, timeout_s: float, service_name: str) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _probe(url, timeout=3.0):
            return
        time.sleep(2.0)
    raise RuntimeError(
        f"{service_name} failed to become ready within {timeout_s:.0f}s; "
        f"health endpoint: {url}"
    )
