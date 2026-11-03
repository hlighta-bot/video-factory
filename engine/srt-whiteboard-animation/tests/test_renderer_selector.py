"""Deterministic renderer selector smoke test."""

from __future__ import annotations

import json
import time
from pathlib import Path

from dataclasses import replace

from scene.model import Scene
from renderers.capabilities import CAPABILITY_PRIORITY
from renderers.router import create_renderer
from renderers.selector import select_renderer


SCRIPT_START = time.time()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
AUTO_TEST_DIR = PROJECT_ROOT / "output" / "auto-test"
RENDERED_OUTPUTS = (
    AUTO_TEST_DIR / "math.mp4",
    AUTO_TEST_DIR / "infographic.mp4",
)


def _scene(renderer: str, **visual: object) -> dict:
    return {
        "id": "selector-test",
        "renderer": renderer,
        "visual": visual,
        "subtitles": [
            {
                "start": "00:00:00,000",
                "end": "00:00:03,000",
                "text": "selector smoke test",
            }
        ],
        "duration_ms": 3000,
    }


def _write_auto_scene(name: str, scene: dict) -> Path:
    path = AUTO_TEST_DIR / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(scene, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> int:
    # Explicit renderer priority.
    for name in ("whiteboard", "svg", "manim"):
        assert select_renderer(_scene(name)) == name, name

    # Deterministic capability priority.
    cases = [
        (_scene("auto", kind="math"), "manim"),
        (_scene("auto", requirements=["formula"]), "manim"),
        (_scene("auto", requirements=["graph"]), "manim"),
        (_scene("auto", kind="infographic"), "svg"),
        (_scene("auto", requirements=["diagram"]), "svg"),
        (_scene("auto", kind="story"), "whiteboard"),
        (_scene("auto", requirements=["historical"]), "whiteboard"),
        (_scene("auto"), "whiteboard"),
        (_scene("auto", requirements=["unknown_capability"]), "whiteboard"),
        # Conflicts use explicit priority: manim before svg before whiteboard.
        (_scene("auto", kind="math", requirements=["infographic"]), "manim"),
        (_scene("auto", kind="infographic", requirements=["story"]), "svg"),
    ]
    for scene, expected in cases:
        actual = select_renderer(scene)
        assert actual == expected, (scene, expected, actual)

    # Router still owns renderer instantiation.
    for name in ("whiteboard", "svg", "manim"):
        assert type(create_renderer(name)).__name__.endswith("Renderer"), name
    try:
        create_renderer("unknown")
    except ValueError:
        pass
    else:
        raise AssertionError("Unknown renderer did not raise ValueError")

    # Auto pipeline samples: render the branches that do not require legacy images/annotations.
    math_scene = _scene("auto", kind="math")
    math_scene["visual"] = {
        "kind": "math",
        "requirements": ["formula", "graph"],
        "manim": {
            "width": 1280,
            "height": 720,
            "elements": [
                {"type": "text", "text": "y = x²", "start_ms": 0, "duration_ms": 800, "animation": "Write"},
                {"type": "axes", "start_ms": 1000, "duration_ms": 900, "animation": "Create"},
                {"type": "graph", "expression": "x^2", "start_ms": 2000, "duration_ms": 1000, "animation": "Create"},
            ],
        },
    }

    infographic_scene = _scene("auto", kind="infographic")
    infographic_scene["visual"] = {
        "kind": "infographic",
        "width": 1280,
        "height": 720,
        "background": "#0f172a",
        "elements": [
            {"kind": "rect", "x": 80, "y": 80, "width": 1120, "height": 560, "rx": 40, "fill": "#111827", "stroke": "#38bdf8", "stroke-width": 6},
            {"kind": "text", "x": 430, "y": 360, "font-size": 72, "fill": "#ffffff", "text": "Auto SVG"},
        ],
    }

    story_scene = _scene("auto", kind="story")
    _write_auto_scene("math", math_scene)
    _write_auto_scene("infographic", infographic_scene)
    _write_auto_scene("story", story_scene)

    for scene in (math_scene, infographic_scene):
        renderer_name = select_renderer(scene)
        renderer = create_renderer(renderer_name)
        render_scene = replace(Scene.from_dict(scene), renderer=renderer_name)
        output = AUTO_TEST_DIR / f"{scene['visual']['kind']}.mp4"
        renderer.render(render_scene, output)
        assert output.exists(), output

    print(f"priority_rules={len(CAPABILITY_PRIORITY)}")
    print(f"auto_tests_dir={AUTO_TEST_DIR}")
    print(f"math_renderer={select_renderer(math_scene)}")
    print(f"infographic_renderer={select_renderer(infographic_scene)}")
    print(f"story_renderer={select_renderer(story_scene)}")
    print(f"rendered_outputs={','.join(str(p.relative_to(PROJECT_ROOT)) for p in RENDERED_OUTPUTS if p.exists())}")
    print(f"elapsed_sec={time.time() - SCRIPT_START:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
