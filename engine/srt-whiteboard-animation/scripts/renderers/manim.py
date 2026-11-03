"""Manim Renderer，支持最小 Manim 场景渲染。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .base import Renderer, SceneLike
from scene.normalizer import normalize_scene


class ManimRenderer(Renderer):
    """将 renderer-specific 的 Manim schema 渲染成 MP4。"""

    def __init__(self, config: str | None = "-qm", media_dir: Path | None = None, **_):
        self.config = config or "-qm"
        self.media_dir = media_dir

    def capabilities(self) -> frozenset[str]:
        return frozenset({"math", "formula", "graph", "geometry", "physics", "scientific_animation"})

    def render(self, scene: SceneLike, output_path: Path) -> Path:
        scene = normalize_scene(scene) if isinstance(scene, dict) else scene
        if scene.renderer != "manim":
            raise ValueError(f"ManimRenderer requires scene.renderer == 'manim', got {scene.renderer!r}")

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        visual = scene.visual or {}
        manim_cfg = visual.get("manim", {})
        code = self._build_script(scene, manim_cfg, output_path)
        temp_dir = Path(tempfile.mkdtemp(prefix="manim-renderer-"))
        script_path = temp_dir / "manim_scene.py"
        script_path.write_text(code, encoding="utf-8")
        media_dir = Path(self.media_dir or temp_dir / "media")

        cmd = [
            sys.executable,
            "-m",
            "manim",
            "render",
            *self.config.split(),
            "--media_dir",
            str(media_dir),
            "--format",
            "mp4",
            str(script_path),
            "Scene_" + self._safe_identifier(scene.id),
            "-o",
            output_path.name,
        ]
        env = os.environ.copy()
        env["PYTHONPATH"] = str(script_path.parent) + os.pathsep + env.get("PYTHONPATH", "")
        subprocess.run(cmd, check=True, env=env, cwd=str(temp_dir), text=True)

        candidates = sorted(media_dir.rglob(output_path.name), key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            candidates = sorted(media_dir.rglob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            raise FileNotFoundError(f"Manim did not produce an MP4 under {media_dir}")
        shutil.copyfile(candidates[0], output_path)
        shutil.rmtree(temp_dir, ignore_errors=True)
        return output_path

    def _build_script(self, scene, manim_cfg: dict[str, Any], output_path: Path) -> str:
        class_name = f"Scene_{self._safe_identifier(scene.id)}"
        lines = [
            "from manim import *",
            "",
            f"class {class_name}(Scene):",
            "    def construct(self):",
            "        self.camera.background_color = 'BLACK'",
        ]

        duration_ms = int(scene.duration_ms or 5000)
        total_sec = duration_ms / 1000.0
        cursor_ms = 0.0
        axes_var: str | None = None

        for index, element in enumerate(manim_cfg.get("elements", [])):
            kind = element.get("type", element.get("kind", "text"))
            anim = element.get("animation", "FadeIn")
            start_ms = float(element.get("start_ms", 0))
            duration_ms_elem = float(element.get("duration_ms", 800))
            run_time = max(0.001, duration_ms_elem / 1000.0)
            gap = max(0.0, (start_ms - cursor_ms) / 1000.0)
            if gap > 0:
                lines.append(f"        self.wait({gap:.3f})")
            cursor_ms = start_ms + duration_ms_elem

            if kind in ("text", "title"):
                content = str(element.get("text", ""))
                var = f"obj_{index}"
                lines.append(f"        {var} = Text({json.dumps(content, ensure_ascii=False)}, font_size=48)")
                self._append_play(lines, var, anim, run_time)
            elif kind == "axes":
                var = f"axes_{index}"
                axes_var = var
                lines.append(f"        {var} = Axes(x_range=[-3, 3, 1], y_range=[-1, 5, 1], x_length=6, y_length=5)")
                self._append_play(lines, var, anim, run_time)
            elif kind in ("graph", "formula"):
                expression = str(element.get("expression", "x^2"))
                var = f"curve_{index}"
                if expression.strip() == "x^2":
                    lines.append(f"        {var} = {axes_var or 'axes_0'}.plot(lambda x: x**2, x_range=[-2.2, 2.2], color=BLUE, stroke_width=6)")
                else:
                    safe_expr = expression.replace("^", "**")
                    lines.append(f"        {var} = {axes_var or 'axes_0'}.plot(lambda x: {safe_expr}, x_range=[-2.2, 2.2], color=BLUE, stroke_width=6)")
                self._append_play(lines, var, anim, run_time)
            elif kind == "circle":
                radius = float(element.get("radius", 1.0))
                var = f"circle_{index}"
                lines.append(f"        {var} = Circle(radius={radius}, color=GREEN)")
                self._append_play(lines, var, anim, run_time)
            else:
                raise ValueError(f"Unsupported Manim element type: {kind!r}")

        remaining = max(0.0, total_sec - (cursor_ms / 1000.0))
        if remaining > 0:
            lines.append(f"        self.wait({remaining:.3f})")
        lines.append(f"        print('MANIM_OUTPUT={output_path.name}')")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _append_play(lines: list[str], var: str, anim: str, run_time: float) -> None:
        if anim == "Write":
            play = "Write"
        elif anim == "Create":
            play = "Create"
        else:
            play = "FadeIn"
        lines.append(f"        self.play({play}({var}), run_time={run_time:.3f})")

    @staticmethod
    def _safe_identifier(name: str) -> str:
        safe = "".join(ch if ch.isalnum() else "_" for ch in str(name))
        if not safe or safe[0].isdigit():
            safe = f"_{safe}"
        return safe
