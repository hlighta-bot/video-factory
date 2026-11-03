"""Phase 24 Asset Library smoke test.

Purpose:
    Verify the manifest-backed Asset Library is deterministic, portable,
    explicit about missing files, and compatible with the existing Asset
    Resolver entry point.

Input:
    - Temporary Phase 24 test assets under output/phase24-test/assets
    - assets/manifest.json as the formal project manifest example

Output:
    - output/phase24/asset-library-report.json
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path

from scene.asset_library import (
    AssetLibraryError,
    load_asset_library,
    load_asset_manifest,
    validate_asset_manifest,
)
from scene.asset_resolver import resolve_assets
from scene.model import Scene
from scene.visual_plan import build_visual_plan


SCRIPT_START = time.time()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_ROOT = PROJECT_ROOT / "output" / "phase24-test"
TEST_ASSETS = TEST_ROOT / "assets"
TEST_MANIFEST = TEST_ASSETS / "manifest.json"
REPORT_ROOT = PROJECT_ROOT / "output" / "phase24"
REPORT_PATH = REPORT_ROOT / "asset-library-report.json"
ROCKET_SOURCE_DIR = PROJECT_ROOT / "output" / "my-rocket"
SUBJECT_PATH = TEST_ASSETS / "subjects" / "rocket.png"
ENVIRONMENT_PATH = TEST_ASSETS / "environments" / "sky.png"


def _ensure_asset(name: Path, source: Path | None = None, png: bool = False) -> Path:
    name.parent.mkdir(parents=True, exist_ok=True)
    if name.exists():
        return name
    if source is not None and source.exists():
        shutil.copyfile(source, name)
        return name
    if png:
        try:
            from PIL import Image
        except Exception:
            name.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 12)
            return name
        image = Image.new("RGB", (1, 1), color=(255, 255, 255))
        image.save(name)
        return name
    raise FileNotFoundError(source or name)


def _write_manifest(payload: dict) -> None:
    TEST_MANIFEST.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _valid_manifest() -> dict:
    return {
        "version": "1.0",
        "assets": [
            {"id": "rocket", "type": "subject", "path": "subjects/rocket.png"},
            {"id": "sky", "type": "environment", "path": "environments/sky.png"},
            {"id": "missing", "type": "subject", "path": "subjects/missing.png"},
        ],
    }


def _assert_error(payload: dict, expected: str) -> None:
    try:
        validate_asset_manifest(payload, root=TEST_ASSETS)
    except AssetLibraryError as exc:
        assert expected in str(exc), str(exc)
    else:
        raise AssertionError(f"expected AssetLibraryError containing {expected!r}")


def _semantic_scene() -> Scene:
    return Scene(
        id="asset-library-test",
        renderer="auto",
        visual={
            "kind": "story",
            "requirements": ["illustration"],
            "intent": "rocket_launch",
            "visual_plan": build_visual_plan("rocket_launch", "rocket launch"),
        },
        narration="test",
        duration_ms=1000,
    )


def main() -> int:
    TEST_ASSETS.mkdir(parents=True, exist_ok=True)
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    SUBJECT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ENVIRONMENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _ensure_asset(SUBJECT_PATH, ROCKET_SOURCE_DIR / "rocket.jpg", png=True)
    _ensure_asset(ENVIRONMENT_PATH, png=True)
    _write_manifest(_valid_manifest())

    manifest = load_asset_manifest(TEST_MANIFEST)
    library = load_asset_library(TEST_MANIFEST)
    summary = library.summary()
    assert summary["version"] == "1.0", summary
    assert summary["asset_count"] == 3, summary
    assert summary["by_type"] == {"subject": 2, "environment": 1}, summary
    assert summary["missing"] == 1, summary

    rocket_a = library.get("rocket")
    rocket_b = library.get("rocket")
    rocket_c = library.get("rocket")
    assert rocket_a == rocket_b == rocket_c, (rocket_a, rocket_b, rocket_c)
    assert rocket_a is not None and rocket_a.exists, rocket_a
    assert rocket_a.to_dict()["path"] == "subjects/rocket.png", rocket_a.to_dict()

    sky = library.get("sky")
    assert sky is not None and sky.exists, sky
    assert sky.to_dict()["path"] == "environments/sky.png", sky.to_dict()

    missing = library.get("missing")
    assert missing is not None and missing.exists is False, missing

    resolution = resolve_assets(_semantic_scene(), asset_library=library)
    assert resolution.assets["subject"]["status"] == "resolved"
    assert resolution.assets["subject"]["path"] == str(SUBJECT_PATH)
    assert resolution.assets["subject"]["library_id"] == "rocket"
    assert resolution.assets["environment"]["status"] == "resolved"
    assert resolution.assets["environment"]["path"] == str(ENVIRONMENT_PATH)
    assert resolution.missing == []

    _assert_error(
        {
            "version": "1.0",
            "assets": [
                {"id": "rocket", "type": "subject", "path": "subjects/rocket.png"},
                {"id": "rocket", "type": "subject", "path": "subjects/rocket2.png"},
            ],
        },
        "duplicate asset id",
    )
    _assert_error({"version": "1.0"}, "assets must be a list")
    _assert_error({"version": "1.0", "assets": [{"id": "rocket"}]}, "missing type")
    _assert_error({"version": "1.0", "assets": [{"id": "rocket", "type": "subject"}]}, "missing path")
    _assert_error({"version": "1.0", "assets": [{"type": "subject", "path": "subjects/rocket.png"}]}, "missing id")
    _assert_error({"version": "2.0", "assets": []}, "unsupported asset manifest version")
    _assert_error({"version": "1.0", "assets": [{"id": "bad", "type": "subject", "path": "../../outside.png"}]}, "path traversal")

    with tempfile.TemporaryDirectory() as tmp_dir:
        bad_path = Path(tmp_dir) / "bad.json"
        bad_path.write_text("{ not valid", encoding="utf-8")
        try:
            load_asset_manifest(bad_path)
        except AssetLibraryError as exc:
            assert "JSON is invalid" in str(exc), str(exc)
        else:
            raise AssertionError("expected invalid JSON error")

    formal_manifest = load_asset_manifest(PROJECT_ROOT / "assets" / "manifest.json")
    assert formal_manifest["version"] == "1.0"
    assert formal_manifest["assets"][0]["id"] == "drawing-hand"
    assert formal_manifest["assets"][0]["path"] == "drawing-hand.png"

    report = {
        "phase": 24,
        "status": "pass",
        "manifest": str(TEST_MANIFEST.relative_to(PROJECT_ROOT)),
        "formal_manifest": "assets/manifest.json",
        "asset_count": summary["asset_count"],
        "missing": summary["missing"],
        "duplicate_ids": 0,
        "path_traversal_rejected": True,
        "invalid_manifest_rejected": True,
        "deterministic": True,
        "resolver_integration": "pass",
        "formal_asset_count": len(formal_manifest["assets"]),
        "elapsed_sec": round(time.time() - SCRIPT_START, 3),
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"manifest={TEST_MANIFEST.relative_to(PROJECT_ROOT)}")
    print(f"asset_count={summary['asset_count']}")
    print(f"missing={summary['missing']}")
    print(f"resolver_integration=pass")
    print(f"report={REPORT_PATH.relative_to(PROJECT_ROOT)}")
    print(f"elapsed_sec={time.time() - SCRIPT_START:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
