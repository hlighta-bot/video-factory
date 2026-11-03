"""Asset Resolver smoke test.

Purpose:
- Verify deterministic local asset resolution from Visual Spec
- Confirm explicit missing-asset behavior
- Keep Phase 14 independent of Bridge and Renderer internals
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from scene.asset_resolver import resolve_assets
from scene.model import Scene
from scene.visual_plan import build_visual_plan
from scene.visual_spec import adapt_whiteboard


SCRIPT_START = time.time()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_ROOT = PROJECT_ROOT / "output" / "phase14-test" / "assets"
SUBJECT_FILE = FIXTURE_ROOT / "rocket.png"
ENVIRONMENT_FILE = FIXTURE_ROOT / "sky.png"


def _ensure_fixture(name: str, extension: str = ".png") -> Path:
    fixture = FIXTURE_ROOT / f"{name}{extension}"
    fixture.parent.mkdir(parents=True, exist_ok=True)
    if not fixture.exists():
        fixture.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 12)
    return fixture


def _scene(intent: str, visual_plan: dict) -> Scene:
    return Scene(
        id="asset-test",
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


def main() -> int:
    _ensure_fixture("rocket")
    _ensure_fixture("sky")

    subject_plan = build_visual_plan("rocket_launch", "rocket launch")
    subject_scene = _scene("rocket_launch", subject_plan)
    spec = adapt_whiteboard(subject_scene).to_dict()
    assert spec["renderer"] == "whiteboard"
    assert {"type": "rocket", "role": "subject"} in spec["elements"]
    assert {"type": "environment", "value": "sky"} in spec["elements"]

    resolved = resolve_assets(subject_scene, asset_roots=[FIXTURE_ROOT])
    assert resolved.to_dict()["renderer"] == "whiteboard"
    assert resolved.assets["subject"]["status"] == "resolved"
    assert resolved.assets["subject"]["path"] == str(SUBJECT_FILE)
    assert resolved.assets["environment"]["status"] == "resolved"
    assert resolved.assets["environment"]["path"] == str(ENVIRONMENT_FILE)
    assert resolved.missing == []

    missing_scene = _scene("rocket_launch", {"subject": "nonexistent_object", "environment": "void"})
    missing_result = resolve_assets(missing_scene, asset_roots=[FIXTURE_ROOT])
    assert missing_result.assets["subject"]["status"] == "missing"
    assert missing_result.assets["subject"]["path"] is None
    assert missing_result.assets["environment"]["status"] == "missing"
    assert missing_result.assets["environment"]["path"] is None
    assert "nonexistent_object" in missing_result.missing
    assert "void" in missing_result.missing

    first = resolve_assets(subject_scene, asset_roots=[FIXTURE_ROOT]).to_dict()
    second = resolve_assets(subject_scene, asset_roots=[FIXTURE_ROOT]).to_dict()
    assert first == second

    report = {
        "subject": resolved.assets["subject"],
        "environment": resolved.assets["environment"],
        "missing": missing_result.missing,
        "fixture_root": str(FIXTURE_ROOT),
    }
    (FIXTURE_ROOT.parent / "resolver-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("resolver=local-deterministic")
    print(f"subject_path={resolved.assets['subject']['path']}")
    print(f"environment_path={resolved.assets['environment']['path']}")
    print(f"missing_subject={missing_result.assets['subject']['status']}")
    print(f"deterministic={'yes' if first == second else 'no'}")
    print(f"fixture_root={FIXTURE_ROOT}")
    print(f"elapsed_sec={time.time() - SCRIPT_START:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
