"""Phase 31 Story -> Automatic Asset -> Video E2E.

Purpose:
    Prove the one-command Story pipeline can run without --image by relying on
    automatic asset resolution from the existing asset roots and annotation policy.

Core logic:
    1. Write a local Canonical Story fixture with a rocket subject.
    2. Run scripts/story_pipeline.py without --image.
    3. Verify the pipeline resolves subject assets, generates annotations when needed,
       and emits an H.264 MP4.

Input data:
    - Local canonical Story JSON fixture

Output data:
    - Generated SRT, scene.json, report JSON, and final MP4 under output/phase31

Notes:
    - This test intentionally exercises the full CLI path rather than importing main() directly.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path


SCRIPT_START = time.time()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PHASE_ROOT = PROJECT_ROOT / "output" / "phase31"
STORY_PATH = PHASE_ROOT / "story.json"
OUTPUT_DIR = PHASE_ROOT / "auto-video"
FINAL_MP4 = OUTPUT_DIR / "auto-video-final.mp4"
REPORT_PATH = PHASE_ROOT / "story-asset-e2e-report.json"
STORY = {
    "version": "1.0",
    "title": "Rocket Launch",
    "segments": [
        {"text": "The rocket ignites and launches into the sky."},
        {"text": "The rocket rises upward."},
        {"text": "The rocket reaches space."},
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

    cmd = [
        str(PROJECT_ROOT / ".venv" / "bin" / "python"),
        str(PROJECT_ROOT / "scripts" / "story_pipeline.py"),
        str(STORY_PATH),
        "--output",
        str(OUTPUT_DIR),
        "--renderer",
        "auto",
        "--tts",
        "edge",
    ]
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True)
    combined = (result.stdout or "") + "\n" + (result.stderr or "")
    assert result.returncode == 0, combined

    assert (OUTPUT_DIR / "story.srt").exists(), "missing generated SRT"
    assert (OUTPUT_DIR / "scene.json").exists(), "missing generated scene artifact"
    assert FINAL_MP4.exists() and FINAL_MP4.stat().st_size > 0, f"missing final MP4: {FINAL_MP4}"

    info = _ffprobe(FINAL_MP4)
    assert info["codec_name"] == "h264"
    assert int(info["width"]) > 0 and int(info["height"]) > 0
    assert float(info["duration"]) > 0

    report = {
        "phase": 31,
        "status": "pass",
        "story": str(STORY_PATH),
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
