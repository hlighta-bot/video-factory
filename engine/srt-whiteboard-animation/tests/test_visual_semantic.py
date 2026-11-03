"""Phase 32B VisualSemantic integration smoke test.

Purpose:
    Verify deterministic semantic extraction, runtime propagation into VisualPlan,
    VisualSpec object emission, and richer procedural fallback primitives.

Input data:
    - Four representative topic texts

Output data:
    - output/phase32b/semantic-integration-report.json
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from scene.model import Scene
from scene.planner import plan_scene
from scene.visual_plan import build_visual_plan
from scene.visual_representation import resolve_visual_representation
from scene.visual_semantic import extract_visual_semantic
from scene.visual_spec import adapt_whiteboard


SCRIPT_START = time.time()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = PROJECT_ROOT / "output" / "phase32b" / "semantic-integration-report.json"
CASES = [
    ("介绍中国的万里长城", "wall", "mountains", "extends", ("mountain", "watchtower")),
    ("介绍日本的富士山", "mountain", "mountain", "show", ("peak",)),
    ("介绍中国的大熊猫", "panda", "forest", "show", ("bamboo",)),
    ("介绍日本的新干线", "train", "railway", "show", ("tracks",)),
    ("云朵旅行到蓝天", "cloud", "sky", "travels", ()),
    ("Clouds Travel Across the Sky", "cloud", "sky", "travels", ()),
    ("小鸟飞过天空", "bird", "sky", "flies", ()),
    ("A Bird Flies Across the Sky", "bird", "sky", "flies", ()),
    ("帆船航行在海面上", "boat", "ocean", "sails", ()),
    ("A Boat Sails Across the Ocean", "boat", "ocean", "sails", ()),
]


def _scene(text: str) -> Scene:
    return plan_scene(text)


def _contains(elements: list[dict], predicate) -> bool:
    return any(predicate(element) for element in elements)


def main() -> int:
    results = []

    for text, expected_subject, expected_environment, expected_action, expected_objects in CASES:
        semantic = extract_visual_semantic(text)
        assert semantic.subject == expected_subject, (text, semantic.to_dict())
        assert semantic.environment == expected_environment, (text, semantic.to_dict())
        assert semantic.action == expected_action, (text, semantic.to_dict())
        assert set(expected_objects).issubset(set(semantic.objects)), (text, semantic.to_dict())
        assert extract_visual_semantic(text).to_dict() == semantic.to_dict()

        scene = _scene(text)
        visual_plan = scene.visual["visual_plan"]
        assert visual_plan["semantic"] == semantic.to_dict(), (text, visual_plan)
        assert visual_plan["subject"] == "scene", visual_plan

        spec = adapt_whiteboard(scene)
        assert _contains(spec.elements, lambda e: e.get("type") == expected_subject and e.get("role") == "subject"), spec.to_dict()
        assert _contains(spec.elements, lambda e: e.get("type") == "action" and e.get("value") == expected_action), spec.to_dict()
        for object_name in expected_objects:
            assert _contains(spec.elements, lambda e, name=object_name: e.get("type") == name and e.get("role") == "object"), spec.to_dict()

        representation = resolve_visual_representation(scene, asset_roots=[])
        assert representation.kind == "procedural_svg", representation.to_dict()
        payload = representation.payload
        assert payload["template"] == "semantic_story_fallback", payload
        assert payload["semantic"] == semantic.to_dict(), payload
        assert any(element.get("kind") == "rect" for element in payload["elements"]), payload
        if expected_subject in {"cloud", "bird"}:
            assert any(element.get("kind") in {"circle", "ellipse"} for element in payload["elements"]), payload
        if expected_subject == "boat":
            assert sum(element.get("kind") == "polygon" for element in payload["elements"]) >= 4, payload

        results.append(
            {
                "text": text,
                "semantic": semantic.to_dict(),
                "visual_plan": visual_plan,
                "spec_elements": spec.elements,
                "representation": representation.to_dict(),
            }
        )

    quad_plan = build_visual_plan("quadratic_function", "y = x²")
    assert quad_plan["formula"] == "x²"
    assert "semantic" in quad_plan

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps({"cases": results, "elapsed_sec": round(time.time() - SCRIPT_START, 3)}, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"cases={len(results)}")
    print(f"report={REPORT_PATH.relative_to(PROJECT_ROOT)}")
    print(f"elapsed_sec={time.time() - SCRIPT_START:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
