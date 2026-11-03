"""Phase 20 Canonical Story → Scene → Asset → Renderer E2E.

Purpose:
- Prove a Scene-architecture path that does not use story_to_srt/video_pipeline.
- Validate Story, adapt to Scene[], resolve visual assets, bridge to Whiteboard,
  render each scene with the existing renderer, and concatenate with existing tools.
- Verify final MP4 with ffprobe.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

from merge_scenes import main as merge_scenes_main
from renderers.router import create_renderer
from scene.asset_bridge import require_ready_bridge
from scene.asset_resolver import resolve_assets
from scene.model import Scene
from scene.story_adapter import story_to_scenes
from scene.visual_spec import adapt_whiteboard
from scene.whiteboard_bridge import bridge_whiteboard
from story_schema import SUPPORTED_VERSION, validate_story


SCRIPT_START = time.time()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PHASE_ROOT = PROJECT_ROOT / "output" / "phase20"
FIXTURE_ROOT = PHASE_ROOT / "assets"
ROCKET_SOURCE_IMAGE = PROJECT_ROOT / "output" / "my-rocket" / "rocket.jpg"
ROCKET_SOURCE_ANNOTATION = PROJECT_ROOT / "output" / "my-rocket" / "rocket.annotation.json"
STORY_PATH = PHASE_ROOT / "story.json"
SCENES_PATH = PHASE_ROOT / "scenes.json"
FINAL_MP4 = PHASE_ROOT / "rocket-launch.mp4"

STORY = {
    "version": SUPPORTED_VERSION,
    "title": "Rocket Launch",
    "segments": [
        {"text": "The rocket ignites and launches into the sky."},
        {"text": "The rocket rises upward."},
        {"text": "The rocket reaches space."},
    ],
}


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


def _ensure_rocket_assets() -> tuple[Path, Path]:
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    image = FIXTURE_ROOT / "rocket.jpg"
    annotation = FIXTURE_ROOT / "rocket.annotation.json"
    if not image.exists():
        if not ROCKET_SOURCE_IMAGE.exists():
            raise FileNotFoundError(f"Missing source rocket image: {ROCKET_SOURCE_IMAGE}")
        shutil.copyfile(ROCKET_SOURCE_IMAGE, image)
    if not annotation.exists():
        if not ROCKET_SOURCE_ANNOTATION.exists():
            raise FileNotFoundError(f"Missing source rocket annotation: {ROCKET_SOURCE_ANNOTATION}")
        shutil.copyfile(ROCKET_SOURCE_ANNOTATION, annotation)
    return image, annotation


def main() -> int:
    PHASE_ROOT.mkdir(parents=True, exist_ok=True)
    _image, annotation = _ensure_rocket_assets()
    STORY_PATH.write_text(json.dumps(STORY, ensure_ascii=False, indent=2), encoding="utf-8")

    validated = validate_story(json.loads(STORY_PATH.read_text(encoding="utf-8")))
    scenes = story_to_scenes(validated)
    assert len(scenes) >= 1, "Story adapter produced no scenes"

    render_inputs: list[Path] = []
    visual_specs = []
    for scene in scenes:
        assert {"sceneId", "narration", "duration_ms", "visual"} <= set(scene.to_dict()), scene.to_dict()
        assert {"kind", "intent", "visual_plan"} <= set(scene.visual), scene.visual
        assert scene.visual["intent"] in {"rocket_launch", "rocket_flying", "rocket_space"}

        spec = adapt_whiteboard(scene)
        visual_specs.append(spec.to_dict())
        assert any(element.get("type") == "rocket" for element in spec.elements), spec.to_dict()

        prepared = Scene(
            id=scene.id,
            renderer=scene.renderer,
            visual={**dict(scene.visual), "annotation": str(annotation)},
            narration=scene.narration,
            subtitles=list(scene.subtitles),
            duration_ms=scene.duration_ms,
        )
        resolution = resolve_assets(prepared, asset_roots=[FIXTURE_ROOT, PROJECT_ROOT / "output" / "my-rocket"])
        assert resolution.assets["subject"]["exists"] is True, resolution.to_dict()
        assert resolution.assets["annotation"]["exists"] is True, resolution.to_dict()

        bridged = require_ready_bridge(prepared, resolution)
        assert bridged.status["ready"] is True, bridged.to_dict()
        assert Path(bridged.scene.visual["image"]).exists(), bridged.scene.visual
        assert Path(bridged.scene.visual["annotation"]).exists(), bridged.scene.visual

        whiteboard_input = bridge_whiteboard(bridged.scene)
        assert whiteboard_input["scene"]["renderer"] == "whiteboard"
        assert Path(whiteboard_input["scene"]["image"]).exists()

        render_scene = Scene.from_dict(whiteboard_input["scene"])
        output_path = PHASE_ROOT / f"scene-{scene.id}.mp4"
        renderer = create_renderer("whiteboard", total_ms=render_scene.duration_ms or max(1500, render_scene.duration_ms))
        generated = renderer.render(render_scene, output_path)
        assert generated.exists() and generated.stat().st_size > 0, generated
        render_inputs.append(generated)

    SCENES_PATH.write_text(
            json.dumps(
                {
                    "story": str(STORY_PATH),
                    "scenes": [scene.to_dict() for scene in scenes],
                    "visual_specs": visual_specs,
                    "render_inputs": [str(path) for path in render_inputs],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    if len(render_inputs) == 1:
        shutil.copyfile(render_inputs[0], FINAL_MP4)
    else:
        assert merge_scenes_main(["--inputs", *[str(path) for path in render_inputs], "--output", str(FINAL_MP4)]) == 0
    assert FINAL_MP4.exists() and FINAL_MP4.stat().st_size > 0, FINAL_MP4

    info = _ffprobe(FINAL_MP4)
    assert info["codec_name"] == "h264", info
    assert int(info["width"]) > 0 and int(info["height"]) > 0, info
    fps = info.get("r_frame_rate", "")
    numerator, denominator = (fps.split("/", 1) + ["1"])[:2]
    assert float(numerator) / float(denominator) > 0, info
    assert float(info["duration"]) > 0, info

    (PHASE_ROOT / "phase20-report.json").write_text(
        json.dumps(
            {
                "story": str(STORY_PATH),
                "scenes": len(scenes),
                "final_mp4": str(FINAL_MP4),
                "codec": info["codec_name"],
                "resolution": f"{info['width']}x{info['height']}",
                "fps": fps,
                "duration_sec": round(float(info["duration"]), 3),
                "elapsed_sec": round(time.time() - SCRIPT_START, 3),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"final_mp4={FINAL_MP4}")
    print(f"codec={info['codec_name']}")
    print(f"resolution={info['width']}x{info['height']}")
    print(f"fps={fps}")
    print(f"duration={float(info['duration']):.3f}")
    print(f"elapsed_sec={time.time() - SCRIPT_START:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
