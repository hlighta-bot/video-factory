"""Cinematic Renderer: fullscreen background image + Ken Burns motion + Chinese
title overlay with shadow, plus fade in/out transitions per scene.

Design:
    - Reuses the Renderer interface (scene -> video clip).
    - Reads scene.visual["image"] as the fullscreen background (resolved by
      Asset Resolver / explicit image path).
    - Reads scene.visual["title"] or derives a short Chinese title from the
      narration / first subtitle.
    - Applies deterministic Ken Burns motion (zoom-in / zoom-out / pan-left /
      pan-right) using FFmpeg zoompan.
    - Draws the unified Chinese title with a soft shadow and fade in/out via
      drawtext alpha expression.
    - Ends every clip with a short alpha fade so boundaries smooth into black
      without changing duration or introducing hard brightness resets.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .base import Renderer, SceneLike
from scene.normalizer import normalize_scene

DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 720
DEFAULT_FPS = 25
DEFAULT_FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
DEFAULT_FADE_S = 0.35
DEFAULT_TITLE_SIZE = 54
ZOOMPAN_SUPERSAMPLE = 2
KENBURNS_STYLES = ("zoom-in", "zoom-out", "pan-right", "pan-left")


def _ff_escape(value: str) -> str:
    """Escape a value for an FFmpeg filtergraph option (inside quotes)."""
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("%", "%%")
    )


def _pick_kenburns(scene) -> str:
    visual = scene.visual or {}
    requested = visual.get("kenburns") or (visual.get("visual_plan") or {}).get("motion")
    if requested:
        return str(requested)
    try:
        index = int("".join(ch for ch in str(scene.id) if ch.isdigit()) or "1")
    except ValueError:
        index = 1
    return KENBURNS_STYLES[(index - 1) % len(KENBURNS_STYLES)]


def _zoompan_expr(
    style: str,
    duration_frames: int,
    output_width: int = DEFAULT_WIDTH,
    output_height: int = DEFAULT_HEIGHT,
    fps: int = DEFAULT_FPS,
) -> str:
    """Return zoompan=... fragment for a deterministic Ken Burns style."""
    duration_frames = max(duration_frames, 1)
    fps = max(int(fps), 1)
    duration_s = duration_frames / fps
    zoom_rate = 0.055 / duration_s
    center_x = "iw/2-(iw/zoom)/2"
    center_y = "ih/2-(ih/zoom)/2"

    if style == "zoom-out":
        z = f"max(1.0,1.055-{zoom_rate:.6f}*time)"
        x, y = center_x, center_y
    elif style == "pan-right":
        z = "1.055"
        x = f"(iw-iw/zoom)*(time/{duration_s:.6f})"
        y = center_y
    elif style == "pan-left":
        z = "1.055"
        x = f"(iw-iw/zoom)*(1-time/{duration_s:.6f})"
        y = center_y
    else:  # zoom-in (default)
        z = f"min(1+{zoom_rate:.6f}*time,1.055)"
        x, y = center_x, center_y

    return (
        f"zoompan=z='{z}':d={duration_frames}:"
        f"x='{x}':y='{y}':s={int(output_width)}x{int(output_height)}:fps={fps}"
    )


class CinematicRenderer(Renderer):
    """Fullscreen cinematic clip renderer."""

    def __init__(
        self,
        fontfile: str = DEFAULT_FONT,
        fps: int = DEFAULT_FPS,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        fade_s: float = DEFAULT_FADE_S,
        title_size: int = DEFAULT_TITLE_SIZE,
    ):
        self.fontfile = fontfile
        self.fps = fps
        self.width = width
        self.height = height
        self.fade_s = fade_s
        self.title_size = title_size

    def capabilities(self) -> frozenset[str]:
        return frozenset({
            "cinematic", "documentary", "fullscreen", "background",
            "kenburns", "story", "illustration", "historical",
        })

    def _scene_duration_s(self, scene) -> float:
        ms = int(scene.duration_ms or 0)
        if ms <= 0 and scene.subtitles:
            last = scene.subtitles[-1]
            ms = int(last.get("end_ms", last.get("end", "0"))) or 0
        return max(1.0, ms / 1000.0)

    def _scene_title(self, scene) -> str:
        visual = scene.visual or {}
        explicit = visual.get("title")
        if explicit:
            title = str(explicit).strip()
        else:
            narration = str(scene.narration or "").strip()
            if narration:
                title = narration
            elif scene.subtitles:
                title = str(scene.subtitles[0].get("text", "")).strip()
            else:
                title = str(visual.get("subject", "scene")).strip()

        # Keep the title on one line without cutting it at an arbitrary
        # eight-character boundary in the middle of a word or phrase.
        title = " ".join(title.split())
        max_chars = 22
        if len(title) > max_chars:
            return title[: max_chars - 1].rstrip() + "…"
        return title

    @staticmethod
    def _fade_filter(duration_s: float, fade_s: float) -> str:
        fade = min(fade_s, duration_s * 0.5)
        fade_out_start = max(0.0, duration_s - fade)
        return (
            f"fade=t=in:st=0:d={fade:.3f}:alpha=0:color=black,"
            f"fade=t=out:st={fade_out_start:.3f}:d={fade:.3f}:alpha=0:color=black,"
        )

    def render(self, scene: SceneLike, output_path: Path) -> Path:
        scene = normalize_scene(scene) if isinstance(scene, dict) else scene
        visual = scene.visual or {}
        image = visual.get("image")
        if not image:
            raise ValueError("CinematicRenderer requires scene.visual['image']")
        image_path = Path(image)
        if not image_path.exists():
            raise FileNotFoundError(image_path)

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        duration_s = self._scene_duration_s(scene)
        total_frames = max(int(round(duration_s * self.fps)), self.fps)
        style = _pick_kenburns(scene)
        fade = min(self.fade_s, duration_s * 0.5)
        fade_out_start = max(0.0, duration_s - fade)
        fade_filter = self._fade_filter(duration_s, self.fade_s)

        # zoompan rounds source crop coordinates to integer pixels. Render at
        # 2x in 4:4:4 so each crop step becomes a subpixel movement after the
        # final downscale, instead of holding and jumping between output pixels.
        zoompan = _zoompan_expr(
            style,
            total_frames,
            output_width=self.width * ZOOMPAN_SUPERSAMPLE,
            output_height=self.height * ZOOMPAN_SUPERSAMPLE,
            fps=self.fps,
        )

        font = _ff_escape(self.fontfile)
        title_alpha = (
            f"if(lt(t\\,{fade})\\,t/{fade}\\,"
            f"if(lt(t\\,{fade_out_start})\\,1\\,max(0\\,({duration_s}-t)/{fade})))"
        )
        drawtext = ""
        if visual.get("show_title", True):
            title = _ff_escape(self._scene_title(scene))
            drawtext = (
                f"drawtext=fontfile='{font}':text='{title}':"
                f"fontsize={self.title_size}:fontcolor=white:"
                f"shadowcolor=black@0.65:shadowx=3:shadowy=3:borderw=0:"
                f"x=(w-text_w)/2:y={int(self.height * 0.09)}:"
                f"alpha='{title_alpha}'"
            )
            drawtext += ","

        vf = (
            f"scale={int(self.width * 1.5 * ZOOMPAN_SUPERSAMPLE)}:-1,"
            f"{zoompan},format=yuv444p,"
            f"scale={self.width}:{self.height}:flags=lanczos,{drawtext}"
            f"{fade_filter}"
            f"format=yuv420p"
        )

        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-loop", "1", "-i", str(image_path),
            "-frames:v", "1",
            "-vf", vf,
            "-frames:v", str(total_frames),
            "-r", str(self.fps),
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p",
            str(output_path),
        ]
        subprocess.run(cmd, check=True)
        return output_path
