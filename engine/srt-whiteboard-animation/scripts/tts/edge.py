"""Edge TTS Provider，使用微软 Edge TTS 服务。"""

import asyncio
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

    def synthesize(self, text: str, output_path: Path) -> Path:
        # Keep Edge TTS optional so the offline provider works without its package.
        try:
            import edge_tts
        except ImportError as exc:
            raise RuntimeError(
                "Edge TTS requires the optional 'edge-tts' package"
            ) from exc

        async def _synthesize():
            communicate = edge_tts.Communicate(
                text=text,
                voice=self.voice,
                rate=self.rate,
                pitch=self.pitch,
            )
            await communicate.save(str(output_path))

        asyncio.run(_synthesize())
        return output_path
