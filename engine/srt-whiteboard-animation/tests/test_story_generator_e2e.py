"""Phase 26 LLM Story Generator -> Scene -> Whiteboard E2E.

Purpose:
- Prove the full chain: topic -> mock story -> Scene[] -> asset bridge -> whiteboard MP4.
- Keep downstream behavior deterministic by using existing Scene and Whiteboard APIs.
- Emit artifacts under output/phase26 for inspection.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import cv2
import numpy as np

from merge_scenes import main as merge_scenes_main
from renderers.router import create_renderer
from scene.asset_bridge import auto_annotate_bridge
from scene.asset_resolver import resolve_assets
from scene.model import Scene
from scene.story_adapter import story_to_scenes
from scene.whiteboard_bridge import bridge_whiteboard
from story.generator import generate_story
from story.router import create_story_provider


SCRIPT_START = time.time()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PHASE_ROOT = PROJECT_ROOT / "output" / "phase26"
FIXTURE_ROOT = PHASE_ROOT / "assets"
ROCKET_SOURCE_IMAGE = PROJECT_ROOT / "output" / "my-rocket" / "rocket.jpg"
ROCKET_SOURCE_ANNOTATION = PROJECT_ROOT / "output" / "my-rocket" / "rocket.annotation.json"
STORY_PATH = PHASE_ROOT / "generated-story.json"
SCENES_PATH = PHASE_ROOT / "scenes.json"
FINAL_MP4 = PHASE_ROOT / "generated-rocket-launch.mp4"


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


def _ensure_assets() -> None:
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    image = FIXTURE_ROOT / "rocket.jpg"
    annotation = FIXTURE_ROOT / "rocket.annotation.json"
    environment = FIXTURE_ROOT / "sky.jpg"

    if not image.exists():
        if not ROCKET_SOURCE_IMAGE.exists():
            raise FileNotFoundError(f"Missing source rocket image: {ROCKET_SOURCE_IMAGE}")
        shutil.copyfile(ROCKET_SOURCE_IMAGE, image)
    if not annotation.exists():
        if not ROCKET_SOURCE_ANNOTATION.exists():
            raise FileNotFoundError(f"Missing source rocket annotation: {ROCKET_SOURCE_ANNOTATION}")
        shutil.copyfile(ROCKET_SOURCE_ANNOTATION, annotation)
    if not environment.exists():
        sky = np.full((256, 256, 3), (120, 170, 245), dtype=np.uint8)
        cv2.imwrite(str(environment), sky)


def main() -> int:
    PHASE_ROOT.mkdir(parents=True, exist_ok=True)
    _ensure_assets()

    story = generate_story("火箭点火", create_story_provider("mock"), provider_name="mock")
    STORY_PATH.write_text(json.dumps(story, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    scenes = story_to_scenes(story)
    assert len(scenes) >= 1
    assert scenes[0].visual["intent"] == "rocket_launch"

    render_inputs: list[Path] = []
    for scene in scenes:
        resolution = resolve_assets(scene, asset_roots=[FIXTURE_ROOT])
        assert resolution.assets["subject"]["exists"] is True, resolution.to_dict()
        assert resolution.assets["environment"]["exists"] is True, resolution.to_dict()

        bridged = auto_annotate_bridge(scene, resolution)
        assert bridged.status["ready"] is True, bridged.to_dict()
        assert bridged.status["subject_status"] == "resolved", bridged.to_dict()
        assert bridged.status["annotation_status"] in {"resolved", "preserved"}, bridged.to_dict()

        whiteboard_input = bridge_whiteboard(bridged.scene)
        assert whiteboard_input["scene"]["renderer"] == "whiteboard"
        assert Path(whiteboard_input["scene"]["image"]).exists()
        assert Path(whiteboard_input["scene"]["visual"]["annotation"]).exists()

        render_scene = Scene.from_dict(whiteboard_input["scene"])
        output_path = PHASE_ROOT / f"scene-{scene.id}.mp4"
        renderer = create_renderer("whiteboard", total_ms=render_scene.duration_ms or max(1500, render_scene.duration_ms))
        generated = renderer.render(render_scene, output_path)
        assert generated.exists() and generated.stat().st_size > 0, generated
        render_inputs.append(generated)

    if len(render_inputs) == 1:
        shutil.copyfile(render_inputs[0], FINAL_MP4)
    else:
        assert merge_scenes_main(["--inputs", *[str(path) for path in render_inputs], "--output", str(FINAL_MP4)]) == 0
    assert FINAL_MP4.exists() and FINAL_MP4.stat().st_size > 0, FINAL_MP4

    info = _ffprobe(FINAL_MP4)
    assert info["codec_name"] == "h264", info
    assert int(info["width"]) > 0 and int(info["height"]) > 0, info
    assert float(info["duration"]) > 0, info

    SCENES_PATH.write_text(
        json.dumps(
            {
                "story": str(STORY_PATH),
                "scenes": [scene.to_dict() for scene in scenes],
                "final_mp4": str(FINAL_MP4),
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    report = {
        "phase": 26,
        "status": "pass",
        "provider": "mock",
        "story": str(STORY_PATH.relative_to(PROJECT_ROOT)),
        "scenes": len(scenes),
        "final_mp4": str(FINAL_MP4.relative_to(PROJECT_ROOT)),
        "codec": info["codec_name"],
        "resolution": f"{info['width']}x{info['height']}",
        "duration_sec": round(float(info["duration"]), 3),
        "elapsed_sec": round(time.time() - SCRIPT_START, 3),
    }
    (PHASE_ROOT / "story-generator-e2e-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"story={STORY_PATH.relative_to(PROJECT_ROOT)}")
    print(f"scenes={len(scenes)}")
    print(f"final_mp4={FINAL_MP4.relative_to(PROJECT_ROOT)}")
    print(f"codec={info['codec_name']}")
    print(f"resolution={info['width']}x{info['height']}")
    print(f"duration={float(info['duration']):.3f}")
    print(f"elapsed_sec={round(time.time() - SCRIPT_START, 3):.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
