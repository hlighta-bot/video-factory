"""Phase 22 Scene Plan Artifact → Rendering Pipeline MVP.

Purpose:
    Prove that scenes.json is a persistent intermediate artifact that can be
    written in one process and consumed by a new process without a live planner.

Core logic:
    1. Producer subprocess validates canonical story, builds Scene[], saves scenes.json.
    2. Consumer subprocess loads scenes.json back into Scene[] and renders via the
       existing Visual Spec / Asset / Bridge / Renderer pipeline.
    3. Parent verifies process separation, generated MP4 metadata, and writes report.

Input data:
    - Canonical story fixture or inline fallback story

Output data:
    - output/phase22/scenes.json
    - output/phase22/process-boundary.mp4
    - output/phase22/phase22-report.json

Dependencies:
    - Scene Plan artifact API
    - Existing Whiteboard rendering pipeline
    - ffprobe
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from merge_scenes import main as merge_scenes_main
from renderers.router import create_renderer
from scene.asset_bridge import require_ready_bridge
from scene.asset_resolver import resolve_assets
from scene.model import Scene
from scene.scene_plan import (
    ScenePlanError,
    load_scene_plan,
    load_scene_scenes,
    save_scene_plan,
    scene_plan_from_dict,
    scene_plan_to_dict,
)
from scene.story_adapter import story_to_scenes
from scene.visual_spec import adapt_whiteboard
from scene.whiteboard_bridge import bridge_whiteboard
from story_schema import SUPPORTED_VERSION, validate_story


SCRIPT_START = time.time()
SCRIPT_PATH = Path(__file__).resolve()
PROJECT_ROOT = SCRIPT_PATH.parent.parent
PHASE_ROOT = PROJECT_ROOT / "output" / "phase22"
FIXTURE_ROOT = PHASE_ROOT / "assets"
FIXTURE_STORY_PATH = PROJECT_ROOT / "tests" / "fixtures" / "story_canonical.json"
ROCKET_SOURCE_IMAGE = PROJECT_ROOT / "output" / "my-rocket" / "rocket.jpg"
ROCKET_SOURCE_ANNOTATION = PROJECT_ROOT / "output" / "my-rocket" / "rocket.annotation.json"
SCENES_PATH = PHASE_ROOT / "scenes.json"
FINAL_MP4 = PHASE_ROOT / "process-boundary.mp4"
REPORT_PATH = PHASE_ROOT / "phase22-report.json"


def _default_story() -> dict:
    # The inline rocket story is intentionally stable for this rendering boundary test:
    # it guarantees Visual Spec values that map to the local rocket assets.
    return validate_story(
        {
            "version": SUPPORTED_VERSION,
            "title": "Rocket Launch",
            "segments": [
                {"text": "The rocket launches into the sky."},
                {"text": "The rocket rises upward."},
                {"text": "The rocket reaches space."},
            ],
        }
    )


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


def _assert_roundtrip(scenes: list[Scene], plan: dict) -> None:
    restored = scene_plan_from_dict(plan)
    assert restored == scenes, (restored, scenes)
    assert scene_plan_to_dict(restored) == plan, (plan, scene_plan_to_dict(restored))


def _assert_invalid_plan(payload: object, expected: str) -> None:
    try:
        scene_plan_from_dict(payload)  # type: ignore[arg-type]
    except ScenePlanError as exc:
        assert expected in str(exc), (expected, str(exc))
    else:
        raise AssertionError(f"expected ScenePlanError containing {expected!r}")


def _write_invalid_plan_probe() -> None:
    _assert_invalid_plan({"version": "1.0"}, "scenes list")
    _assert_invalid_plan({"version": "2.0", "scenes": []}, "unsupported scene plan version")
    _assert_invalid_plan({"scenes": []}, "unsupported scene plan version")
    _assert_invalid_plan("not-a-plan", "JSON object")
    with tempfile.TemporaryDirectory() as tmp_dir:
        bad_path = Path(tmp_dir) / "bad.json"
        bad_path.write_text("{ not valid", encoding="utf-8")
        try:
            load_scene_scenes(bad_path)
        except ScenePlanError as exc:
            assert "JSON is invalid" in str(exc), str(exc)
        else:
            raise AssertionError("expected ScenePlanError for invalid JSON")


def _producer_main() -> int:
    PHASE_ROOT.mkdir(parents=True, exist_ok=True)
    _ensure_rocket_assets()
    story = _default_story()
    scenes = story_to_scenes(story)
    assert len(scenes) >= 1
    plan = scene_plan_to_dict(scenes)
    _assert_roundtrip(scenes, plan)
    save_scene_plan(scenes, SCENES_PATH)
    print(json.dumps({"role": "producer", "scene_count": len(scenes), "scenes": str(SCENES_PATH), "pid": os.getpid()}))
    return 0


def _consumer_main() -> int:
    _ensure_rocket_assets()
    _, annotation = _ensure_rocket_assets()
    artifact = load_scene_plan(SCENES_PATH)
    scenes = load_scene_scenes(SCENES_PATH)
    assert artifact["scenes"] == [scene.to_dict() for scene in scenes]
    assert len(scenes) >= 1

    render_inputs: list[Path] = []
    visual_specs: list[dict] = []
    for scene in scenes:
        spec = adapt_whiteboard(scene)
        visual_specs.append(spec.to_dict())
        assert spec.renderer == "whiteboard", spec.to_dict()
        assert len(spec.elements) >= 1, spec.to_dict()

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
        whiteboard_input = bridge_whiteboard(bridged.scene)
        render_scene = Scene.from_dict(whiteboard_input["scene"])
        renderer = create_renderer("whiteboard", total_ms=render_scene.duration_ms or 1500)
        generated = renderer.render(render_scene, PHASE_ROOT / f"scene-{scene.id}.mp4")
        assert generated.exists() and generated.stat().st_size > 0, generated
        render_inputs.append(generated)

    if len(render_inputs) == 1:
        shutil.copyfile(render_inputs[0], FINAL_MP4)
    else:
        assert merge_scenes_main(["--inputs", *[str(path) for path in render_inputs], "--output", str(FINAL_MP4)]) == 0
    assert FINAL_MP4.exists() and FINAL_MP4.stat().st_size > 0

    info = _ffprobe(FINAL_MP4)
    print(
        json.dumps(
            {
                "role": "consumer",
                "scene_count": len(scenes),
                "visual_spec_count": len(visual_specs),
                "final_mp4": str(FINAL_MP4),
                "codec": info["codec_name"],
                "resolution": f"{info['width']}x{info['height']}",
                "fps": info["r_frame_rate"],
                "duration_sec": round(float(info["duration"]), 3),
                "pid": os.getpid(),
            },
            sort_keys=True,
        )
    )
    return 0


def _run_process(role: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SCRIPT_PATH.parent)
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--role", role],
        check=False,
        cwd=SCRIPT_PATH.parent,
        env=env,
        capture_output=True,
        text=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 22 Scene Plan artifact rendering boundary test")
    parser.add_argument("--role", choices=["producer", "consumer"], help="Run as producer or consumer subprocess")
    args = parser.parse_args()

    if args.role == "producer":
        return _producer_main()
    if args.role == "consumer":
        return _consumer_main()

    PHASE_ROOT.mkdir(parents=True, exist_ok=True)
    _ensure_rocket_assets()
    _write_invalid_plan_probe()

    producer = _run_process("producer")
    assert producer.returncode == 0, producer.stderr or producer.stdout
    producer_info = json.loads(producer.stdout.strip().splitlines()[-1])
    assert producer_info["role"] == "producer"
    assert producer_info["pid"] != os.getpid(), producer_info
    assert SCENES_PATH.exists()

    consumer = _run_process("consumer")
    assert consumer.returncode == 0, consumer.stderr or consumer.stdout
    consumer_info = json.loads(consumer.stdout.strip().splitlines()[-1])
    assert consumer_info["role"] == "consumer"
    assert consumer_info["pid"] != os.getpid(), consumer_info
    assert consumer_info["scene_count"] == producer_info["scene_count"]
    assert consumer_info["final_mp4"] == str(FINAL_MP4)

    info = _ffprobe(FINAL_MP4)
    assert info["codec_name"] == "h264"
    assert int(info["width"]) > 0 and int(info["height"]) > 0
    fps = info.get("r_frame_rate", "")
    numerator, denominator = (fps.split("/", 1) + ["1"])[:2]
    assert float(numerator) / float(denominator) > 0
    assert float(info["duration"]) > 0

    report = {
        "phase": 22,
        "status": "pass",
        "artifact": "output/phase22/scenes.json",
        "scene_count": producer_info["scene_count"],
        "producer_pid": producer_info["pid"],
        "consumer_pid": consumer_info["pid"],
        "final_mp4": "output/phase22/process-boundary.mp4",
        "codec": info["codec_name"],
        "resolution": f"{info['width']}x{info['height']}",
        "fps": fps,
        "duration_sec": round(float(info["duration"]), 3),
        "elapsed_sec": round(time.time() - SCRIPT_START, 3),
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"artifact={SCENES_PATH}")
    print(f"producer_pid={producer_info['pid']}")
    print(f"consumer_pid={consumer_info['pid']}")
    print(f"final_mp4={FINAL_MP4}")
    print(f"codec={info['codec_name']}")
    print(f"resolution={info['width']}x{info['height']}")
    print(f"fps={fps}")
    print(f"duration={float(info['duration']):.3f}")
    print(f"report={REPORT_PATH}")
    print(f"elapsed_sec={time.time() - SCRIPT_START:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
