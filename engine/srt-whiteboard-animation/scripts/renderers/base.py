from abc import ABC, abstractmethod
from pathlib import Path


class Renderer(ABC):
    """Renderer 抽象基类。

    Renderer 只负责：Scene → video。
    不负责 TTS、SRT、LLM、Story 或最终视频合成。
    """

    @abstractmethod
    def render(
        self,
        scene: dict,
        output_path: Path,
    ) -> Path:
        """将 Scene 渲染为视频。

        Args:
            scene: 场景数据字典。
            output_path: 视频文件输出路径。

        Returns:
            生成的视频文件路径。
        """
        raise NotImplementedError
