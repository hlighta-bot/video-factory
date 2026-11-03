"""Minimal SVG animation renderer smoke test.

Purpose:
- Load an animated SVG Scene JSON file
- Render through SVGRenderer
- Print artifact paths for ffprobe and frame sampling checks
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from scene.model import Scene
from renderers.router import create_renderer

SCRIPT_START = time.time()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCENE = PROJECT_ROOT / "output" / "svg-test" / "animated" / "animated.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "output" / "svg-test" / "animated" / "animated.mp4"


def main() -> int:
    scene_data = json.loads(DEFAULT_SCENE.read_text(encoding="utf-8"))
    scene = Scene.from_dict(scene_data)
    renderer = create_renderer(scene.renderer)
    generated = renderer.render(scene, DEFAULT_OUTPUT)

    print(f"scene_path={DEFAULT_SCENE}")
    print(f"renderer={type(renderer).__name__}")
    print(f"frames_dir={DEFAULT_OUTPUT.parent / (DEFAULT_OUTPUT.stem + '.frames')}")
    print(f"svg_path={DEFAULT_OUTPUT.parent / (DEFAULT_OUTPUT.stem + '.frames') / 'latest.svg'}")
    print(f"mp4_path={generated}")
    print(f"elapsed_sec={time.time() - SCRIPT_START:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
