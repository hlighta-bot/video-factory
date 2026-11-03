"""Auto Annotation CLI compatibility smoke test.

Purpose:
    Lock in both current CLI behavior and the legacy 3-argument invocation used by
    older video_pipeline callers, while leaving generate_annotation() unchanged.

Input data:
    - A local image fixture with simple drawable content

Output data:
    - output/phase25-test/auto-annotation-cli/... artifacts
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from auto_annotation import generate_annotation


SCRIPT_START = time.time()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_ROOT = PROJECT_ROOT / "output" / "phase25-test" / "auto-annotation-cli"
IMAGE_PATH = TEST_ROOT / "rocket.png"
SRT_PATH = TEST_ROOT / "story.srt"
NEW_OUTPUT_PATH = TEST_ROOT / "new-cli.annotation.json"
LEGACY_OUTPUT_PATH = TEST_ROOT / "legacy-cli.annotation.json"


def _ensure_fixture() -> None:
    TEST_ROOT.mkdir(parents=True, exist_ok=True)
    if not IMAGE_PATH.exists():
        image = np.full((320, 320, 3), 250, dtype=np.uint8)
        cv2.rectangle(image, (70, 70), (150, 220), (40, 40, 40), 6)
        cv2.circle(image, (210, 120), 40, (40, 40, 40), 6)
        cv2.imwrite(str(IMAGE_PATH), image)
    SRT_PATH.write_text(
        "1\n00:00:01,000 --> 00:00:09,300\ntest cue\n",
        encoding="utf-8",
    )


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "auto_annotation.py"), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _assert_annotation(path: Path) -> None:
    assert path.exists(), path
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data.get("elements"), list), data
    assert data.get("canvas", {}).get("width") == 320, data
    assert data.get("canvas", {}).get("height") == 320, data


def main() -> int:
    _ensure_fixture()
    NEW_OUTPUT_PATH.unlink(missing_ok=True)
    LEGACY_OUTPUT_PATH.unlink(missing_ok=True)

    direct = generate_annotation(
        IMAGE_PATH,
        output_path=TEST_ROOT / "direct-api.annotation.json",
        scene_id="direct-api",
        scene_duration_ms=9300,
    )
    assert direct.output_path is not None
    assert direct.output_path.exists()

    _run_cli(str(IMAGE_PATH), str(NEW_OUTPUT_PATH), "--duration-ms", "9300")
    _assert_annotation(NEW_OUTPUT_PATH)

    _run_cli(str(IMAGE_PATH), str(SRT_PATH), str(LEGACY_OUTPUT_PATH))
    _assert_annotation(LEGACY_OUTPUT_PATH)

    print(f"direct_api={direct.output_path.relative_to(PROJECT_ROOT)}")
    print(f"new_cli={NEW_OUTPUT_PATH.relative_to(PROJECT_ROOT)}")
    print(f"legacy_cli={LEGACY_OUTPUT_PATH.relative_to(PROJECT_ROOT)}")
    print(f"elapsed_sec={time.time() - SCRIPT_START:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
