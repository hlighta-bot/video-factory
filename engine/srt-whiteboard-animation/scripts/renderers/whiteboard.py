"""Whiteboard Renderer，调用现有白板渲染实现。"""

import importlib.util
from pathlib import Path

from .base import Renderer

# 定位 scripts/ 目录，通过 importlib 加载 render_stream_whiteboard 模块
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _SCRIPTS_DIR.parent

_spec = importlib.util.spec_from_file_location(
    "render_stream_whiteboard",
    _SCRIPTS_DIR / "render_stream_whiteboard.py",
)
_rsw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_rsw)


class WhiteboardRenderer(Renderer):
    """白板动画 Renderer，调用现有白板渲染实现。

    通过 importlib 加载 render_stream_whiteboard.py 并调用其 main(argv)，
    避免复制白板渲染代码。
    """

    def __init__(
        self,
        hand: str | None = None,
        ink_path: str = "grid",
        color_fill: str = "contour-wipe",
        pause: str = "heavy",
        fps: int | None = None,
        grid_edge: int | None = None,
        brush_radius: int | None = None,
        cap_long_edge: int | None = None,
        total_ms: int | None = None,
        bare_tip: bool = False,
    ):
        self.hand = hand
        self.ink_path = ink_path
        self.color_fill = color_fill
        self.pause = pause
        self.fps = fps
        self.grid_edge = grid_edge
        self.brush_radius = brush_radius
        self.cap_long_edge = cap_long_edge
        self.total_ms = total_ms
        self.bare_tip = bare_tip

    def render(self, scene: dict, output_path: Path) -> Path:
        # 从 scene 中获取 image 和 annotation 路径
        image = Path(scene["image"])
        annotation = Path(scene["annotation"])
        hand = self.hand or str(_SCRIPTS_DIR.parent / "assets" / "drawing-hand.png")

        # 构造 render_stream_whiteboard.main(argv) 参数
        argv = [str(image), str(annotation), str(output_path), hand]

        if self.total_ms is not None:
            argv.extend(["--total-ms", str(self.total_ms)])
        if self.bare_tip:
            argv.append("--bare-tip")
        if self.ink_path != "grid":
            argv.extend(["--ink-path", self.ink_path])
        if self.color_fill != "contour-wipe":
            argv.extend(["--color-fill", self.color_fill])
        if self.pause != "heavy":
            argv.extend(["--pause", self.pause])
        if self.fps is not None:
            argv.extend(["--fps", str(self.fps)])
        if self.grid_edge is not None:
            argv.extend(["--grid-edge", str(self.grid_edge)])
        if self.brush_radius is not None:
            argv.extend(["--brush-radius", str(self.brush_radius)])
        if self.cap_long_edge is not None:
            argv.extend(["--cap-long-edge", str(self.cap_long_edge)])

        _rsw.main(argv)

        return output_path
