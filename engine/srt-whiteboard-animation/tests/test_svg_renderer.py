"""Minimal SVG renderer smoke test.

Purpose:
- Load an SVG Scene JSON file
- Create the renderer through the renderer router
- Render Scene -> SVG artifact + MP4
- Print generated artifact paths for downstream ffprobe checks
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from scene.model import Scene
from renderers.router import create_renderer

SCRIPT_START = time.time()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCENE = PROJECT_ROOT / "output" / "svg-test" / "svg-test.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "output" / "svg-test" / "svg-test.mp4"


def main() -> int:
    scene_path = DEFAULT_SCENE
    output_path = DEFAULT_OUTPUT

    scene_data = json.loads(scene_path.read_text(encoding="utf-8"))
    scene = Scene.from_dict(scene_data)
    renderer = create_renderer(scene.renderer)
    generated = renderer.render(scene, output_path)

    print(f"scene_path={scene_path}")
    print(f"renderer={type(renderer).__name__}")
    print(f"svg_path={generated.parent / (generated.stem + '.svg')}")
    print(f"mp4_path={generated}")
    print(f"elapsed_sec={time.time() - SCRIPT_START:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
