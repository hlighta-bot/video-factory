"""Phase 29 Real LLM Story -> Deterministic Scene Pipeline Integration MVP.

Purpose:
- Verify a real OpenAI-compatible Story Provider can produce canonical, visualizable Story JSON.
- Keep the test optional so offline regression does not depend on network/API availability.
- Reuse the existing deterministic pipeline instead of introducing new classifiers or providers.

Core logic:
1. Skip cleanly when OPENAI_API_KEY is not configured.
2. Generate one canonical Story from one real LLM call.
3. Validate Story schema and adapt it through the existing Scene Planner.
4. Prove the same Story always produces the same Scene artifacts.
5. Optionally run the existing asset/annotation/whiteboard pipeline when local fixtures allow it.

Input data:
- topic: "火箭发射"
- env: OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL

Output data:
- output/phase29/generated-story.json when a real LLM call succeeds
- output/phase29/scenes.json when Story -> Scene succeeds
- output/phase29/integration-report.json
- output/phase29/generated-rocket-launch.mp4 when E2E render succeeds
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from merge_scenes import main as merge_scenes_main
from renderers.router import create_renderer
from scene.asset_bridge import auto_annotate_bridge
from scene.asset_resolver import resolve_assets
from scene.model import Scene
from scene.planner import infer_visual_intent
from scene.story_adapter import story_to_scenes
from scene.whiteboard_bridge import bridge_whiteboard
from story.generator import StoryGenerationError, generate_story
from story.router import create_story_provider
from story_schema import StoryValidationError, validate_story


SCRIPT_START = time.time()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PHASE_ROOT = PROJECT_ROOT / "output" / "phase29"
FIXTURE_ROOT = PHASE_ROOT / "assets"
ROCKET_SOURCE_IMAGE = PROJECT_ROOT / "output" / "my-rocket" / "rocket.jpg"
ROCKET_SOURCE_ANNOTATION = PROJECT_ROOT / "output" / "my-rocket" / "rocket.annotation.json"
STORY_PATH = PHASE_ROOT / "generated-story.json"
SCENES_PATH = PHASE_ROOT / "scenes.json"
REPORT_PATH = PHASE_ROOT / "integration-report.json"
FINAL_MP4 = PHASE_ROOT / "generated-rocket-launch.mp4"
TOPIC = "火箭发射"
MIN_SEGMENT_CHARS = 4


def _write_report(report: dict[str, Any]) -> None:
    PHASE_ROOT.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _main() -> int:
    PHASE_ROOT.mkdir(parents=True, exist_ok=True)

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    base_url = os.getenv("OPENAI_BASE_URL", "").strip()
    model = os.getenv("OPENAI_MODEL", "").strip()

    if not api_key:
        report = {
            "phase": 29,
            "status": "skipped",
            "reason": "OPENAI_API_KEY is not configured",
            "topic": TOPIC,
            "provider": "openai-compatible",
            "base_url_configured": bool(base_url),
            "model_configured": bool(model),
            "story_valid": False,
            "scene_count": 0,
            "intents": [],
            "intent_counts": {},
            "story_scene_count": 0,
            "asset_resolution": "not_attempted",
            "renderer": "whiteboard",
            "e2e_render": False,
            "elapsed_sec": round(time.time() - SCRIPT_START, 3),
        }
        _write_report(report)
        print(f"status=skipped reason={report['reason']}")
        print(f"report={REPORT_PATH.relative_to(PROJECT_ROOT)}")
        print(f"elapsed_sec={report['elapsed_sec']:.3f}")
        return 0

    _ensure_assets()

    try:
        provider = create_story_provider("openai")
        story = generate_story(TOPIC, provider, provider_name="openai")
    except (RuntimeError, StoryGenerationError, StoryValidationError) as exc:
        category = "schema_error" if isinstance(exc, StoryValidationError) else "provider_error"
        report = {
            "phase": 29,
            "status": "fail",
            "failure": category,
            "provider": "openai-compatible",
            "base_url": base_url or None,
            "model": model or None,
            "api_key_present": True,
            "story_valid": False,
            "scene_count": 0,
            "intents": [],
            "intent_counts": {},
            "story_scene_count": 0,
            "asset_resolution": "not_attempted",
            "renderer": "whiteboard",
            "e2e_render": False,
            "error": str(exc),
            "elapsed_sec": round(time.time() - SCRIPT_START, 3),
        }
        _write_report(report)
        print(f"status=fail failure={category}")
        print(f"error={exc}")
        print(f"report={REPORT_PATH.relative_to(PROJECT_ROOT)}")
        print(f"elapsed_sec={report['elapsed_sec']:.3f}")
        return 1

    validate_story(story)
    STORY_PATH.write_text(json.dumps(story, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    segment_texts = [segment["text"] for segment in story["segments"]]
    assert len(segment_texts) >= 1
    assert all(text.strip() for text in segment_texts)
    assert all(len(text) >= MIN_SEGMENT_CHARS for text in segment_texts)

    scenes = story_to_scenes(story)
    scenes_again = story_to_scenes(story)
    assert len(scenes) >= 1
    assert [scene.to_dict() for scene in scenes] == [scene.to_dict() for scene in scenes_again]

    intents = [scene.visual.get("intent") or infer_visual_intent(scene.narration) for scene in scenes]
    intent_counts: dict[str, int] = {}
    for intent in intents:
        intent_counts[intent] = intent_counts.get(intent, 0) + 1
    story_scene_count = intent_counts.get("story_scene", 0)
    visualizability_warning = story_scene_count >= max(1, len(scenes) // 2)

    SCENES_PATH.write_text(
        json.dumps(
            {
                "story": str(STORY_PATH.relative_to(PROJECT_ROOT)),
                "scenes": [scene.to_dict() for scene in scenes],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    asset_status, render_ready_scenes = _resolve_scenes(scenes)
    e2e_render = False
    codec = resolution = duration = None
    if render_ready_scenes == len(scenes):
        final_mp4 = _render_final_video(scenes)
        e2e_render = True
        info = _ffprobe(final_mp4)
        codec = info["codec_name"]
        resolution = f"{info['width']}x{info['height']}"
        duration = round(float(info["duration"]), 3)

    status = "pass" if not visualizability_warning else "pass_with_warning"
    report = {
        "phase": 29,
        "status": status,
        "provider": "openai-compatible",
        "base_url": base_url or None,
        "model": model or None,
        "api_key_present": True,
        "api_call": True,
        "story_valid": True,
        "title": story["title"],
        "segments": segment_texts,
        "segment_count": len(segment_texts),
        "scene_count": len(scenes),
        "intents": intents,
        "intent_counts": intent_counts,
        "story_scene_count": story_scene_count,
        "visualizability_warning": visualizability_warning,
        "asset_resolution": asset_status,
        "renderer": "whiteboard",
        "e2e_render": e2e_render,
        "final_mp4": str(FINAL_MP4.relative_to(PROJECT_ROOT)) if e2e_render else None,
        "codec": codec,
        "resolution": resolution,
        "duration_sec": duration,
        "elapsed_sec": round(time.time() - SCRIPT_START, 3),
    }
    _write_report(report)

    print(f"status={status}")
    print(f"provider=openai-compatible model={model or 'default'}")
    print(f"story={STORY_PATH.relative_to(PROJECT_ROOT)}")
    print(f"scenes={SCENES_PATH.relative_to(PROJECT_ROOT)}")
    print(f"segments={len(segment_texts)}")
    print(f"scene_count={len(scenes)}")
    print(f"intents={','.join(intents)}")
    print(f"asset_resolution={asset_status}")
    print(f"e2e_render={str(e2e_render).lower()}")
    if e2e_render:
        print(f"final_mp4={FINAL_MP4.relative_to(PROJECT_ROOT)}")
        print(f"codec={codec}")
        print(f"resolution={resolution}")
        print(f"duration={duration}")
    print(f"report={REPORT_PATH.relative_to(PROJECT_ROOT)}")
    print(f"elapsed_sec={report['elapsed_sec']:.3f}")
    return 0


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


def _resolve_scenes(scenes: list[Scene]) -> tuple[str, int]:
    resolved_count = 0
    missing_scenes = 0
    for scene in scenes:
        resolution = resolve_assets(scene, asset_roots=[FIXTURE_ROOT])
        subject_exists = bool(resolution.assets.get("subject", {}).get("exists"))
        environment_exists = bool(resolution.assets.get("environment", {}).get("exists"))
        if subject_exists and environment_exists:
            resolved_count += 1
        else:
            missing_scenes += 1

    if resolved_count == len(scenes):
        return f"all_resolved:{resolved_count}/{len(scenes)}", resolved_count
    if resolved_count:
        return f"partial:{resolved_count}/{len(scenes)} missing:{missing_scenes}", resolved_count
    return f"missing:{missing_scenes}/{len(scenes)}", resolved_count


def _render_final_video(scenes: list[Scene]) -> Path:
    render_inputs: list[Path] = []
    for scene in scenes:
        resolution = resolve_assets(scene, asset_roots=[FIXTURE_ROOT])
        bridged = auto_annotate_bridge(scene, resolution)
        whiteboard_input = bridge_whiteboard(bridged.scene)
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
    return FINAL_MP4


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
    return _main()


if __name__ == "__main__":
    raise SystemExit(main())
