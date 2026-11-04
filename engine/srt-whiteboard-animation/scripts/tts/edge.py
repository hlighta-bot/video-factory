"""Edge TTS Provider，使用微软 Edge TTS 服务。"""

import asyncio
import time
from pathlib import Path

from .base import TTSProvider


class EdgeTTSProvider(TTSProvider):
    """Edge TTS Provider，使用微软 Edge TTS 服务。"""

    backend_name = "edge"

    def __init__(
        self,
        voice: str = "zh-CN-XiaoxiaoNeural",
        rate: str = "-10%",
        pitch: str = "-1Hz",
    ):
        self.voice = voice
        self.rate = rate
        self.pitch = pitch

    # The public Edge endpoint intermittently returns an empty stream
    # (``NoAudioReceived``) for a perfectly valid request. Retrying the same
    # cue a couple of times keeps a whole render from failing on a transient
    # hiccup; a permanently unreachable endpoint still surfaces as an error.
    MAX_ATTEMPTS = 3

    def synthesize(self, text: str, output_path: Path) -> Path:
        # Keep Edge TTS optional so the offline provider works without its package.
        try:
            import edge_tts
        except ImportError as exc:
            raise RuntimeError(
                "Edge TTS requires the optional 'edge-tts' package"
            ) from exc

        target = Path(output_path)

        async def _synthesize():
            communicate = edge_tts.Communicate(
                text=text,
                voice=self.voice,
                rate=self.rate,
                pitch=self.pitch,
            )
            await communicate.save(str(target))

        last_error: Exception | None = None
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            try:
                asyncio.run(_synthesize())
            except Exception as exc:  # NoAudioReceived, connection reset, ...
                last_error = exc
            else:
                if target.is_file() and target.stat().st_size > 0:
                    return target
                last_error = RuntimeError("Edge TTS returned an empty audio file")
            if attempt < self.MAX_ATTEMPTS:
                print(
                    f"[warn] Edge TTS attempt {attempt}/{self.MAX_ATTEMPTS} failed "
                    f"({type(last_error).__name__}); retrying"
                )
                time.sleep(1.5 * attempt)

        raise RuntimeError(
            f"Edge TTS failed after {self.MAX_ATTEMPTS} attempts "
            f"({type(last_error).__name__}: {last_error}). "
            "Pass --voice-profile en-us-local-narrator / zh-cn-natural to use a "
            "local offline voice instead of the network voice."
        )
