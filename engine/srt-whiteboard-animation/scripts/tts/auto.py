"""Use Audio8 first and explicitly fall back to local eSpeak if requested."""

from pathlib import Path

from .audio8 import Audio8TTSProvider
from .base import TTSProvider
from .espeak import EspeakTTSProvider


class AutoTTSProvider(TTSProvider):
    """Prefer the local neural Audio8 voice; keep one voice for the full job."""

    backend_name = "audio8"

    def __init__(self, **kwargs):
        self._audio8 = Audio8TTSProvider(
            voice="default",
            rate=kwargs.get("rate", "-10%"),
            pitch=kwargs.get("pitch", "-1Hz"),
        )
        self._offline = EspeakTTSProvider(
            voice=kwargs.get("voice", "zh-CN-XiaoxiaoNeural"),
            rate=kwargs.get("rate", "-10%"),
            pitch=kwargs.get("pitch", "-1Hz"),
        )
        self._use_offline = False

    @property
    def using_offline(self) -> bool:
        return self._use_offline

    @property
    def fallback_used(self) -> bool:
        return self._use_offline

    @property
    def backend_name(self) -> str:
        return "espeak" if self._use_offline else "audio8"

    @property
    def voice(self) -> str:
        return "zh" if self._use_offline else self._audio8.voice

    @property
    def service_url(self) -> str | None:
        return None if self._use_offline else self._audio8.service_url

    @property
    def model_name(self) -> str | None:
        return None if self._use_offline else self._audio8.model_name

    @property
    def sample_rate(self) -> int | None:
        return None if self._use_offline else self._audio8.sample_rate

    @property
    def channels(self) -> int | None:
        return None if self._use_offline else self._audio8.channels

    @property
    def request_count(self) -> int:
        return 0 if self._use_offline else self._audio8.request_count

    @property
    def chunk_count(self) -> int:
        return 0 if self._use_offline else self._audio8.chunk_count

    @property
    def warnings(self) -> list[str]:
        warnings = self._audio8.warnings
        if self._use_offline:
            warnings.append("Audio8 unavailable; narration was regenerated with robotic eSpeak voice")
        return warnings

    def synthesize(self, text: str, output_path: Path) -> Path:
        if self._use_offline:
            return self._offline.synthesize(text, output_path)
        try:
            return self._audio8.synthesize(text, output_path)
        except Exception as audio8_error:
            self._use_offline = True
            print(
                "[warn] Audio8 TTS failed; switching to offline eSpeak-NG for "
                "the remaining narration. Voice quality will be synthetic. "
                f"Cause: {audio8_error}"
            )
            try:
                return self._offline.synthesize(text, output_path)
            except Exception as offline_error:
                raise RuntimeError(
                    "Both Audio8 and offline eSpeak-NG failed. "
                    f"Audio8: {audio8_error}; offline: {offline_error}"
                ) from offline_error
