"""Phase 21 Scene Plan Artifact smoke test."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from scene.model import Scene
from scene.scene_plan import (
    ScenePlanError,
    load_scene_plan,
    load_scene_scenes,
    save_scene_plan,
    scene_plan_from_dict,
    scene_plan_to_dict,
    scene_plan_to_json,
)
from scene.story_adapter import story_to_scenes
from story_schema import SUPPORTED_VERSION, validate_story


SCRIPT_START = time.time()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PHASE_ROOT = PROJECT_ROOT / "output" / "phase21"
FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "story_canonical.json"
STORY_PATH = PHASE_ROOT / "story.json"
SCENES_PATH = PHASE_ROOT / "scenes.json"
REPORT_PATH = PHASE_ROOT / "phase21-report.json"


def _sample_scenes() -> list[Scene]:
    return [
        Scene(
            id="scene-01",
            renderer="whiteboard",
            visual={
                "kind": "story",
                "intent": "rocket_launch",
                "visual_plan": {
                    "subject": "rocket",
                    "action": "launch",
                    "environment": "sky",
                    "motion": "upward",
                },
                "image": Path("output/phase21/assets/rocket.jpg"),
            },
            narration="The rocket launches into the sky.",
            subtitles=[{"id": "s1", "start": "00:00:00,000", "end": "00:00:03,000", "text": "The rocket launches."}],
            duration_ms=3000,
        )
    ]


def _assert_plan_schema(plan: dict) -> None:
    assert plan["version"] == "1.0", plan
    assert isinstance(plan["scenes"], list) and plan["scenes"], plan
    first = plan["scenes"][0]
    assert {"sceneId", "renderer", "duration_ms", "narration", "subtitles", "visual"} <= set(first), first
    assert first["sceneId"] == "scene-01"
    assert first["duration_ms"] > 0
    assert first["visual"]["intent"] == "rocket_launch"


def _assert_no_runtime_state(plan: dict) -> None:
    forbidden = {"render_start_time", "machine_path", "temporary_directory", "ffmpeg_pid", "process_id"}
    assert not forbidden.intersection(plan.keys()), plan
    for scene in plan["scenes"]:
        assert not forbidden.intersection(scene.keys()), scene


def main() -> int:
    PHASE_ROOT.mkdir(parents=True, exist_ok=True)
    scenes = _sample_scenes()
    plan = scene_plan_to_dict(scenes)
    _assert_plan_schema(plan)
    json.loads(scene_plan_to_json(scenes))
    _assert_no_runtime_state(plan)

    saved_path = save_scene_plan(scenes, SCENES_PATH)
    loaded = load_scene_plan(saved_path)
    assert loaded == plan, (loaded, plan)
    assert scene_plan_to_dict(scene_plan_from_dict(loaded)) == loaded, loaded
    assert scene_plan_to_dict(load_scene_scenes(saved_path)) == loaded, (saved_path, loaded)
    json.loads(json.dumps(loaded))

    story = validate_story(json.loads(FIXTURE_PATH.read_text(encoding="utf-8")))
    story_scenes = story_to_scenes(story)
    assert len(story_scenes) >= 1
    story_plan = scene_plan_to_dict(story_scenes)
    _assert_plan_schema(story_plan)
    assert json.dumps(story_plan, sort_keys=True) == json.dumps(scene_plan_to_dict(story_scenes), sort_keys=True)
    _assert_no_runtime_state(story_plan)

    STORY_PATH.write_text(json.dumps(story, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    save_scene_plan(story_scenes, SCENES_PATH)
    REPORT = {
        "phase": 21,
        "status": "pass",
        "scene_count": len(story_scenes),
        "scene_plan": "output/phase21/scenes.json",
        "story": "output/phase21/story.json",
        "deterministic": True,
        "json_serializable": True,
        "elapsed_sec": round(time.time() - SCRIPT_START, 3),
    }
    REPORT_PATH.write_text(json.dumps(REPORT, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"scene_count={len(story_scenes)}")
    print(f"scene_plan={SCENES_PATH}")
    print(f"report={REPORT_PATH}")
    print(f"elapsed_sec={time.time() - SCRIPT_START:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
