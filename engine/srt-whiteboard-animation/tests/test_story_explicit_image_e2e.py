"""Phase 32A Explicit Image Priority E2E.

Purpose:
    Prove explicit --image still wins over local assets and fallback paths in
    the full Story -> SRT -> Video pipeline.

Core logic:
    1. Write a local canonical Story fixture.
    2. Run scripts/story_pipeline.py with --image pointing at drawing-hand.png.
    3. Verify the final MP4 exists and is H.264.

Input data:
    - Local canonical Story JSON fixture
    - assets/drawing-hand.png

Output data:
    - Generated SRT, scene.json, report JSON, and final MP4 under output/phase32
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path


SCRIPT_START = time.time()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PHASE_ROOT = PROJECT_ROOT / "output" / "phase32"
STORY_PATH = PHASE_ROOT / "explicit-image-story.json"
OUTPUT_DIR = PHASE_ROOT / "explicit-image-video"
IMAGE_PATH = PROJECT_ROOT / "assets" / "drawing-hand.png"
FINAL_MP4 = OUTPUT_DIR / "drawing-hand-final.mp4"
REPORT_PATH = PHASE_ROOT / "story-explicit-image-e2e-report.json"
STORY = {
    "version": "1.0",
    "title": "Explicit Image Topic",
    "segments": [
        {"text": "This topic would normally fall back."},
        {"text": "But an explicit image is provided."},
        {"text": "The manual image path remains highest priority."},
    ],
}


def _ffprobe(path: Path) -> dict[str, str]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
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
    PHASE_ROOT.mkdir(parents=True, exist_ok=True)
    STORY_PATH.write_text(json.dumps(STORY, ensure_ascii=False, indent=2), encoding="utf-8")
    assert IMAGE_PATH.exists(), f"missing explicit image: {IMAGE_PATH}"

    cmd = [
        str(PROJECT_ROOT / ".venv" / "bin" / "python"),
        str(PROJECT_ROOT / "scripts" / "story_pipeline.py"),
        str(STORY_PATH),
        "--output",
        str(OUTPUT_DIR),
        "--image",
        str(IMAGE_PATH),
        "--renderer",
        "whiteboard",
        "--tts",
        "edge",
    ]
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True)
    combined = (result.stdout or "") + "\n" + (result.stderr or "")
    assert result.returncode == 0, combined

    assert (OUTPUT_DIR / "explicit-image-story.srt").exists(), "missing generated SRT"
    assert (OUTPUT_DIR / "scene.json").exists(), "missing generated scene artifact"
    assert FINAL_MP4.exists() and FINAL_MP4.stat().st_size > 0, f"missing final MP4: {FINAL_MP4}"

    info = _ffprobe(FINAL_MP4)
    assert info["codec_name"] == "h264"
    assert int(info["width"]) > 0 and int(info["height"]) > 0
    assert float(info["duration"]) > 0

    report = {
        "phase": 32,
        "status": "pass",
        "story": str(STORY_PATH),
        "image": str(IMAGE_PATH),
        "output_dir": str(OUTPUT_DIR),
        "final_mp4": str(FINAL_MP4),
        "codec": info["codec_name"],
        "resolution": f"{info['width']}x{info['height']}",
        "fps": info.get("r_frame_rate", ""),
        "duration_sec": round(float(info["duration"]), 3),
        "elapsed_sec": round(time.time() - SCRIPT_START, 3),
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"status={report['status']}")
    print(f"final_mp4={FINAL_MP4}")
    print(f"codec={report['codec']}")
    print(f"resolution={report['resolution']}")
    print(f"duration={report['duration_sec']:.3f}")
    print(f"elapsed_sec={report['elapsed_sec']:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
