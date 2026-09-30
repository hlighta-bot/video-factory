from abc import ABC, abstractmethod
from pathlib import Path


class TTSProvider(ABC):
    """TTS Provider 抽象基类。

    Provider 只负责：text → audio file。
    不负责 SRT 解析、FFmpeg 音频混音或视频合成。
    """

    @abstractmethod
    def synthesize(
        self,
        text: str,
        output_path: Path,
    ) -> Path:
        """将文本生成语音文件。

        Args:
            text: 要合成的文本。
            output_path: 音频文件输出路径。

        Returns:
            生成的音频文件路径。
        """
        raise NotImplementedError
