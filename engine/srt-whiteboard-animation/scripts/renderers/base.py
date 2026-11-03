from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    from scene.model import Scene

SceneLike = Union["Scene", dict]


class Renderer(ABC):
    """Renderer 抽象基类。

    Renderer 只负责：Scene → video。
    不负责 TTS、SRT、LLM、Story 或最终视频合成。
    """

    @abstractmethod
    def render(
        self,
        scene: SceneLike,
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

    def capabilities(self) -> frozenset[str]:
        """声明该 Renderer 支持的能力标签。"""
        return frozenset()
