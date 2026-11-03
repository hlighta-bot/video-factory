"""Deterministic scene planner smoke test."""

from __future__ import annotations

import json
import time
from pathlib import Path

from renderers.selector import select_renderer
from scene.planner import classify_text, infer_visual_intent, plan_scene, plan_script, plan_srt, plan_srt_grouped
from scene.visual_plan import build_visual_plan
from parse_srt import parse_srt


SCRIPT_START = time.time()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PLANNER_TEST_DIR = PROJECT_ROOT / "output" / "planner-test"


SRT_TWO_CUES = """1
00:00:00,000 --> 00:00:02,500
The quadratic formula x = (-b ± √(b² - 4ac)) / 2a.

2
00:00:03,000 --> 00:00:06,000
This infographic compares trend data in a chart.
"""

SRT_GROUPED_ROCKET = """1
00:00:00,000 --> 00:00:01,500
火箭点火

2
00:00:01,500 --> 00:00:03,000
发动机产生巨大的推力

3
00:00:03,000 --> 00:00:04,500
火箭开始升空

4
00:00:04,500 --> 00:00:06,000
它冲向天空

5
00:00:06,000 --> 00:00:09,000
最终突破大气层，飞向太空
"""

SRT_SHORT_FINAL = """1
00:00:00,000 --> 00:00:05,000
The first explanation is introduced.

2
00:00:05,000 --> 00:00:06,000
A tiny final note.
"""


def _assert_scene(
    scene,
    expected_kind: str,
    expected_requirements: list[str],
    expected_renderer: str,
    expected_intent: str | None = None,
) -> None:
    assert scene.renderer == "auto", scene.to_dict()
    assert scene.visual["kind"] == expected_kind, scene.to_dict()
    assert scene.visual["requirements"] == expected_requirements, scene.to_dict()
    assert select_renderer(scene) == expected_renderer, scene.to_dict()
    if expected_intent is not None:
        assert scene.visual.get("intent") == expected_intent, scene.to_dict()


def _cues(srt_text: str) -> list[dict]:
    return parse_srt(srt_text)


def main() -> int:
    # Classification and selector compatibility.
    math_scene = plan_scene("Explain the formula y = x² and graph it.", scene_id="math")
    _assert_scene(math_scene, "math", ["formula", "graph"], "manim", "quadratic_function")

    infographic_scene = plan_scene("Show a data chart comparing growth.", scene_id="data")
    _assert_scene(infographic_scene, "infographic", ["data", "text"], "svg", "data_change")

    story_scene = plan_scene("A historical character explains the story.", scene_id="story")
    _assert_scene(story_scene, "story", ["illustration"], "whiteboard", "story_scene")

    unknown_scene = plan_scene("Hello from the narrator.", scene_id="unknown")
    _assert_scene(unknown_scene, "story", ["illustration"], "whiteboard", "story_scene")

    explicit_scene = plan_scene("Any content.", scene_id="explicit", renderer="svg")
    assert explicit_scene.renderer == "svg", explicit_scene.to_dict()
    assert select_renderer(explicit_scene) == "svg", explicit_scene.to_dict()

    # SRT support: one scene per cue, with timings preserved.
    scenes = plan_script(SRT_TWO_CUES)
    assert [scene.id for scene in scenes] == ["scene-01", "scene-02"], [s.id for s in scenes]
    assert [scene.duration_ms for scene in scenes] == [2500, 3000], [s.duration_ms for s in scenes]
    assert [scene.narration for scene in scenes] == [
        "The quadratic formula x = (-b ± √(b² - 4ac)) / 2a.",
        "This infographic compares trend data in a chart.",
    ], [s.narration for s in scenes]
    _assert_scene(scenes[0], "math", ["formula", "graph"], "manim", "quadratic_function")
    _assert_scene(scenes[1], "infographic", ["data", "text"], "svg", "data_change")
    assert scenes[0].subtitles[0]["start"] == "00:00:00,000", scenes[0].subtitles
    assert scenes[0].subtitles[0]["end"] == "00:00:02,500", scenes[0].subtitles
    assert scenes[1].subtitles[0]["start"] == "00:00:03,000", scenes[1].subtitles
    assert scenes[1].subtitles[0]["end"] == "00:00:06,000", scenes[1].subtitles

    # Grouped planning keeps Phase 9 behavior stable but coalesces short cues.
    grouped_cues = _cues(SRT_GROUPED_ROCKET)
    grouped_scenes = plan_srt_grouped(grouped_cues, target_duration_ms=8000, min_duration_ms=4000, max_duration_ms=12000)
    assert [scene.id for scene in grouped_scenes] == ["scene-01", "scene-02"], [s.id for s in grouped_scenes]
    assert [scene.duration_ms for scene in grouped_scenes] == [3000, 6000], [s.duration_ms for s in grouped_scenes]
    assert [scene.subtitle_count for scene in grouped_scenes] == [2, 3], [s.subtitle_count for s in grouped_scenes]
    assert grouped_scenes[0].narration == "火箭点火发动机产生巨大的推力", grouped_scenes[0].to_dict()
    assert grouped_scenes[1].narration == "火箭开始升空它冲向天空最终突破大气层，飞向太空", grouped_scenes[1].to_dict()

    # No cue split, continuous timeline, and subtitle preservation.
    assert grouped_scenes[0].subtitles[0]["start_ms"] == 0, grouped_scenes[0].subtitles
    assert grouped_scenes[0].subtitles[-1]["end_ms"] == 3000, grouped_scenes[0].subtitles
    assert grouped_scenes[1].subtitles[0]["start_ms"] == 3000, grouped_scenes[1].subtitles
    assert grouped_scenes[1].subtitles[-1]["end_ms"] == 9000, grouped_scenes[1].subtitles
    assert sum(len(scene.subtitles) for scene in grouped_scenes) == len(grouped_cues)
    assert sum(len(scene.narration) for scene in grouped_scenes) == sum(len(cue["text"]) for cue in grouped_cues)
    _assert_scene(grouped_scenes[0], "story", ["illustration"], "whiteboard", "rocket_launch")
    _assert_scene(grouped_scenes[1], "story", ["illustration"], "whiteboard", "rocket_space")

    # Short final scene merges back into the previous scene when allowed.
    short_final_scenes = plan_srt_grouped(_cues(SRT_SHORT_FINAL), target_duration_ms=8000, min_duration_ms=4000, max_duration_ms=12000)
    assert [scene.id for scene in short_final_scenes] == ["scene-01"], [s.id for s in short_final_scenes]
    assert short_final_scenes[0].duration_ms == 6000, short_final_scenes[0].to_dict()
    assert short_final_scenes[0].subtitle_count == 2, short_final_scenes[0].to_dict()

    # Visual intent is deterministic and stable across representative cases.
    intent_cases = {
        "火箭点火": "rocket_launch",
        "Rocket Launch": "rocket_launch",
        "火箭冲向天空": "rocket_flying",
        "火箭突破大气层": "rocket_space",
        "Rocket Flying in Space": "rocket_space",
        "二次函数 y=x²": "quadratic_function",
        "日本人口下降": "population_change",
        "一个普通故事。": "story_scene",
    }
    for text, expected_intent in intent_cases.items():
        assert infer_visual_intent(text) == expected_intent, (text, infer_visual_intent(text), expected_intent)
    assert classify_text("火箭点火") == ("story", ["illustration"]), classify_text("火箭点火")
    assert classify_text("日本人口下降") == ("infographic", ["data", "text"]), classify_text("日本人口下降")

    # Selector regression: intent does not bypass existing kind-based routing.
    assert select_renderer(plan_scene("火箭点火", scene_id="rocket-launch")) == "whiteboard"
    assert select_renderer(plan_scene("二次函数 y=x²", scene_id="quadratic")) == "manim"
    assert select_renderer(plan_scene("日本人口下降", scene_id="population")) == "svg"

    # Visual plan stays renderer-agnostic and deterministic.
    assert math_scene.visual["visual_plan"]["subject"] == "parabola"
    assert infographic_scene.visual["visual_plan"]["subject"] == "data"
    assert unknown_scene.visual["visual_plan"]["subject"] == "scene"
    assert build_visual_plan("unknown_intent")["subject"] == "scene"
    assert build_visual_plan("quadratic_function", "y = x²")["formula"] == "x²"

    # Public Scene serialization remains JSON-compatible.
    payloads = [scene.to_dict() for scene in grouped_scenes]
    serialized = json.dumps(payloads, ensure_ascii=False)
    assert "sceneId" in serialized, serialized
    assert "requirements" in serialized, serialized
    assert "intent" in serialized, serialized

    # Optional CLI artifact: keep outputs traceable without expanding project scope.
    PLANNER_TEST_DIR.mkdir(parents=True, exist_ok=True)
    (PLANNER_TEST_DIR / "planned-scenes.json").write_text(json.dumps(payloads, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"scenes={len(scenes)}")
    print(f"grouped_scenes={len(grouped_scenes)}")
    print(f"short_final_scenes={len(short_final_scenes)}")
    print(f"math_renderer={select_renderer(scenes[0])}")
    print(f"infographic_renderer={select_renderer(scenes[1])}")
    print(f"rocket_launch_intent={grouped_scenes[0].visual.get('intent')}")
    print(f"rocket_space_intent={grouped_scenes[1].visual.get('intent')}")
    print(f"planner_test_dir={PLANNER_TEST_DIR}")
    print(f"elapsed_sec={time.time() - SCRIPT_START:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
