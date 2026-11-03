"""Phase 19 Canonical Story → Scene Adapter smoke test.

Purpose:
- Verify canonical Story is converted into Scene[] via the existing planner APIs.
- Verify deterministic Scene content, intent, visual plan, and serialization.
- Verify invalid canonical Story is blocked before any planner work.
- Verify legacy normalization is opt-in and does not change canonical behavior.
- Verify downstream Visual Spec adaptation still works on adapted scenes.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from renderers.selector import select_renderer
from scene.model import Scene
from scene.story_adapter import StoryAdapterError, story_to_scenes
from scene.visual_spec import adapt_whiteboard
from story_schema import SUPPORTED_VERSION


SCRIPT_START = time.time()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "story_canonical.json"
TEST_ROOT = PROJECT_ROOT / "output" / "phase19-test"
REPORT = TEST_ROOT / "story-adapter-report.json"


def _canonical_story() -> dict:
    return {
        "version": SUPPORTED_VERSION,
        "title": "Rocket Launch",
        "segments": [
            {"text": "火箭点火"},
            {"text": "二次函数 y=x²"},
            {"text": "日本人口下降"},
        ],
    }


def _assert_scene_json(scene: Scene) -> None:
    data = scene.to_dict()
    assert data["renderer"] == scene.renderer
    assert data["narration"] == scene.narration
    assert data["subtitleCount"] == len(scene.subtitles)
    assert data["visual"]["intent"] == scene.visual["intent"]
    assert data["visual"]["visual_plan"]["subject"]


def main() -> int:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert len(story_to_scenes(fixture)) == len(fixture["segments"])
    story = _canonical_story()
    scenes = story_to_scenes(story)

    assert len(scenes) == len(story["segments"]), [scene.id for scene in scenes]
    assert all(isinstance(scene, Scene) for scene in scenes)
    assert [scene.id for scene in scenes] == ["scene-01", "scene-02", "scene-03"]
    assert [scene.renderer for scene in scenes] == ["auto"] * len(scenes)
    assert scenes[0].visual["intent"] == "rocket_launch"
    assert scenes[1].visual["intent"] == "quadratic_function"
    assert scenes[2].visual["intent"] == "population_change"
    assert select_renderer(scenes[0]) == "whiteboard"
    assert select_renderer(scenes[1]) == "manim"
    assert select_renderer(scenes[2]) == "svg"
    for scene in scenes:
        assert scene.duration_ms > 0, scene.to_dict()
        assert scene.subtitles, scene.to_dict()
        _assert_scene_json(scene)

    assert adapt_whiteboard(scenes[0]).renderer == "whiteboard"
    assert adapt_whiteboard(scenes[2]).renderer == "whiteboard"

    legacy_story = {"title": "Legacy", "narration": ["火箭点火", "火箭开始升空"]}
    legacy_scenes = story_to_scenes(legacy_story, allow_legacy=True)
    assert len(legacy_scenes) == 2, [scene.id for scene in legacy_scenes]
    assert legacy_scenes[0].visual["intent"] == "rocket_launch"

    try:
        story_to_scenes({"version": "1.0", "title": "Bad", "segments": []})
        raise AssertionError("expected StoryAdapterError for invalid canonical story")
    except StoryAdapterError as exc:
        assert "expected at least 1 segment" in str(exc), exc

    try:
        story_to_scenes(legacy_story)
        raise AssertionError("expected StoryAdapterError for legacy shape without allow_legacy")
    except StoryAdapterError as exc:
        assert "unknown field(s)" in str(exc) or "story.version: required" in str(exc), exc

    TEST_ROOT.mkdir(parents=True, exist_ok=True)
    report = {
        "fixture": str(FIXTURE_PATH),
        "scenes": len(scenes),
        "legacy_scenes": len(legacy_scenes),
        "renderer": "adapter -> planner",
        "elapsed_sec": round(time.time() - SCRIPT_START, 3),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"fixture={FIXTURE_PATH}")
    print(f"scenes={len(scenes)}")
    print(f"legacy_scenes={len(legacy_scenes)}")
    print(f"report={REPORT}")
    print(f"elapsed_sec={time.time() - SCRIPT_START:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
