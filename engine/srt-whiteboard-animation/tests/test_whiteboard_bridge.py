"""Whiteboard Bridge smoke test.

Purpose:
- Verify Visual Spec -> Bridge -> Whiteboard input conversion
- Render one real rocket scene end-to-end using existing assets
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from renderers.router import create_renderer
from scene.model import Scene
from scene.whiteboard_bridge import bridge_whiteboard


SCRIPT_START = time.time()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_ROOT = PROJECT_ROOT / "output" / "phase13"
ROCKET_SOURCE_DIR = PROJECT_ROOT / "output" / "my-rocket"
ROCKET_IMAGE = ROCKET_SOURCE_DIR / "rocket.jpg"
ROCKET_ANNOTATION = ROCKET_SOURCE_DIR / "rocket.annotation.json"
ROCKET_SCENE = ROCKET_SOURCE_DIR / "scene.json"
ROCKET_OUTPUT = TEST_ROOT / "rocket-launch.mp4"


def _ffprobe(path: Path) -> dict[str, str]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name,width,height,r_frame_rate",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    data: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            data[key] = value
    return data


def main() -> int:
    TEST_ROOT.mkdir(parents=True, exist_ok=True)

    scene_data = json.loads(ROCKET_SCENE.read_text(encoding="utf-8"))
    scene = Scene.from_dict(scene_data)
    scene.visual["annotation"] = str(ROCKET_ANNOTATION)
    bridge_output = bridge_whiteboard(scene)
    assert bridge_output["scene"]["renderer"] == "whiteboard"
    assert bridge_output["spec"]["renderer"] == "whiteboard"

    render_scene = Scene.from_dict(bridge_output["scene"])
    renderer = create_renderer("whiteboard")
    generated = renderer.render(render_scene, ROCKET_OUTPUT)
    assert generated.exists() and generated.stat().st_size > 0

    info = _ffprobe(generated)
    assert info["codec_name"] == "h264"
    assert info["width"] == "1080"
    assert info["height"] == "1080"
    duration = float(info["duration"])
    assert duration > 0

    print("bridge_scene=whiteboard")
    print(f"output_path={generated}")
    print(f"duration={duration:.3f}")
    print(f"codec_name={info['codec_name']}")
    print(f"resolution={info['width']}x{info['height']}")
    print(f"fps={info.get('r_frame_rate', 'n/a')}")
    print(f"elapsed_sec={time.time() - SCRIPT_START:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
