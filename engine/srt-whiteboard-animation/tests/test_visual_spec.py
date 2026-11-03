"""Whiteboard Visual Spec smoke test."""

from __future__ import annotations

import json
import time
from pathlib import Path

from renderers.selector import select_renderer
from scene.model import Scene
from scene.visual_spec import VisualSpec, adapt_whiteboard


SCRIPT_START = time.time()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
VISUAL_SPEC_TEST_DIR = PROJECT_ROOT / "output" / "visual-spec-test"


def _scene(intent: str, visual_plan: dict) -> Scene:
    return Scene(
        id="spec-test",
        renderer="auto",
        visual={
            "kind": "story",
            "requirements": ["illustration"],
            "intent": intent,
            "visual_plan": visual_plan,
        },
        narration="test",
        duration_ms=1000,
    )


def _rocket_launch_plan() -> dict:
    return {"subject": "rocket", "action": "launch", "environment": "sky", "motion": "upward"}


def _rocket_flying_plan() -> dict:
    return {"subject": "rocket", "action": "fly", "environment": "sky", "motion": "forward"}


def _rocket_space_plan() -> dict:
    return {"subject": "rocket", "action": "travel", "environment": "space", "motion": "forward"}


def _story_plan() -> dict:
    return {"subject": "scene", "action": "show", "environment": "story", "focus": "narrative"}


def _contains(elements: list[dict], predicate) -> bool:
    return any(predicate(element) for element in elements)


def main() -> int:
    launch_spec: VisualSpec = adapt_whiteboard(_scene("rocket_launch", _rocket_launch_plan()))
    assert launch_spec.renderer == "whiteboard"
    assert _contains(launch_spec.elements, lambda e: e.get("type") == "rocket" and e.get("role") == "subject")
    assert _contains(launch_spec.elements, lambda e: e.get("type") == "environment" and e.get("value") == "sky")
    assert _contains(launch_spec.elements, lambda e: e.get("type") == "motion" and e.get("direction") == "upward")

    flying_spec: VisualSpec = adapt_whiteboard(_scene("rocket_flying", _rocket_flying_plan()))
    assert flying_spec.renderer == "whiteboard"
    assert _contains(flying_spec.elements, lambda e: e.get("type") == "rocket")
    assert _contains(flying_spec.elements, lambda e: e.get("value") == "sky")
    assert _contains(flying_spec.elements, lambda e: e.get("direction") == "forward")

    space_spec: VisualSpec = adapt_whiteboard(_scene("rocket_space", _rocket_space_plan()))
    assert _contains(space_spec.elements, lambda e: e.get("type") == "rocket")
    assert _contains(space_spec.elements, lambda e: e.get("value") == "space")
    assert _contains(space_spec.elements, lambda e: e.get("direction") == "forward")

    story_spec: VisualSpec = adapt_whiteboard(_scene("story_scene", _story_plan()))
    assert _contains(story_spec.elements, lambda e: e.get("type") == "scene" and e.get("role") == "subject")
    assert _contains(story_spec.elements, lambda e: e.get("value") == "story")

    fallback_spec: VisualSpec = adapt_whiteboard(_scene("unknown_intent", {}))
    assert fallback_spec.renderer == "whiteboard"
    assert _contains(fallback_spec.elements, lambda e: e.get("type") == "scene")
    assert _contains(fallback_spec.elements, lambda e: e.get("value") == "story")

    real_scene_data = {
        "id": "rocket-01",
        "renderer": "auto",
        "visual": {
            "kind": "story",
            "requirements": ["illustration"],
            "intent": "rocket_launch",
            "visual_plan": _rocket_launch_plan(),
        },
    }
    real_scene = Scene.from_dict(real_scene_data)
    scene_spec = adapt_whiteboard(real_scene)
    assert scene_spec.renderer == "whiteboard"
    assert _contains(scene_spec.elements, lambda e: e.get("type") == "rocket")
    assert select_renderer(real_scene) == "whiteboard"
    assert select_renderer({"id": "quad", "renderer": "auto", "visual": {"kind": "math", "requirements": ["formula"]}}) == "manim"
    assert select_renderer({"id": "pop", "renderer": "auto", "visual": {"kind": "infographic", "requirements": ["data"]}}) == "svg"

    serialized = json.dumps(
        {
            "scene": real_scene.to_dict(),
            "spec": scene_spec.to_dict(),
        },
        ensure_ascii=False,
    )
    assert "visual_plan" in serialized
    assert "visual_spec" not in serialized

    VISUAL_SPEC_TEST_DIR.mkdir(parents=True, exist_ok=True)
    (VISUAL_SPEC_TEST_DIR / "visual-spec.json").write_text(json.dumps(scene_spec.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    print("intent=rocket_launch")
    print("spec_path=output/visual-spec-test/visual-spec.json")
    print(f"selector_rocket={select_renderer(real_scene)}")
    print(f"selector_math=manim")
    print(f"selector_population=svg")
    print(f"visual_spec_test_dir={VISUAL_SPEC_TEST_DIR}")
    print(f"elapsed_sec={time.time() - SCRIPT_START:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
