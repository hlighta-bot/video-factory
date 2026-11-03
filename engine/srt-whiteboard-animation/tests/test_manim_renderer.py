"""Minimal Manim renderer smoke test."""

from __future__ import annotations

import json
import time
from pathlib import Path

from renderers.router import create_renderer


SCRIPT_START = time.time()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCENE = PROJECT_ROOT / "output" / "manim-test" / "manim-test.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "output" / "manim-test" / "manim-test.mp4"


def main() -> int:
    # Router checks
    wb = create_renderer("whiteboard")
    svg = create_renderer("svg")
    manim = create_renderer("manim")
    if type(wb).__name__ != "WhiteboardRenderer" or type(svg).__name__ != "SVGRenderer" or type(manim).__name__ != "ManimRenderer":
        raise AssertionError(f"Unexpected renderer classes: {wb}, {svg}, {manim}")
    try:
        create_renderer("unknown")
    except ValueError:
        pass
    else:
        raise AssertionError("Unknown renderer did not raise ValueError")

    scene_data = json.loads(DEFAULT_SCENE.read_text(encoding="utf-8"))
    renderer = create_renderer(scene_data["renderer"])
    generated = renderer.render(scene_data, DEFAULT_OUTPUT)

    print(f"scene_path={DEFAULT_SCENE}")
    print(f"renderer={type(renderer).__name__}")
    print(f"mp4_path={generated}")
    print(f"elapsed_sec={time.time() - SCRIPT_START:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
