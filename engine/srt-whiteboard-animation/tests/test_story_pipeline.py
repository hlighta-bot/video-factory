"""Phase 17 Story → SRT → Phase 16 pipeline smoke test.

Purpose:
- Verify deterministic Story → SRT generation.
- Verify generated SRT is parseable and non-overlapping.
- Verify empty/missing Story inputs fail explicitly.
- Verify Story → SRT → existing Phase 16 pipeline can produce an MP4.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from parse_srt import parse_srt
from story_pipeline import main_cli
from story_to_srt import build_srt_from_story, write_srt_from_story


SCRIPT_START = time.time()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_ROOT = PROJECT_ROOT / "output" / "phase17-test"
STORY_PATH = TEST_ROOT / "rocket-story.json"
GENERATED_SRT = TEST_ROOT / "generated.srt"
EMPTY_STORY_PATH = TEST_ROOT / "empty-story.json"
ASSET_ROOT = PROJECT_ROOT / "output" / "my-rocket"
IMAGE_PATH = ASSET_ROOT / "rocket.jpg"
OUTPUT_DIR = TEST_ROOT / "mp4-output"
REPORT = TEST_ROOT / "story-pipeline-report.json"


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
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    stream = (payload.get("streams") or [{}])[0]
    return {
        "codec_name": str(stream.get("codec_name", "")),
        "width": str(stream.get("width", "")),
        "height": str(stream.get("height", "")),
        "r_frame_rate": str(stream.get("r_frame_rate", "")),
        "duration": str((payload.get("format") or {}).get("duration", "")),
    }


def _write_story() -> None:
    TEST_ROOT.mkdir(parents=True, exist_ok=True)
    STORY_PATH.write_text(
        json.dumps(
            {
                "version": "1.0",
                "title": "Rocket launch",
                "segments": [
                    {"text": "火箭点火，发动机产生巨大的推力。"},
                    {"text": "火箭开始升空，它冲向天空。"},
                    {"text": "最终突破大气层，飞向太空。"},
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    EMPTY_STORY_PATH.write_text(json.dumps({"version": "1.0", "title": "Empty", "segments": []}), encoding="utf-8")


def main() -> int:
    _write_story()

    srt_text = build_srt_from_story(json.loads(STORY_PATH.read_text(encoding="utf-8")))
    cues = parse_srt(srt_text)
    assert len(cues) == 3, cues
    assert cues[0]["text"] == "火箭点火，发动机产生巨大的推力。", cues
    assert cues[1]["text"] == "火箭开始升空，它冲向天空。", cues
    assert cues[2]["text"] == "最终突破大气层，飞向太空。", cues
    for i, cue in enumerate(cues[:-1]):
        assert cue["endMs"] < cues[i + 1]["startMs"], cues
        assert cue["durMs"] > 0, cue
    assert build_srt_from_story(json.loads(STORY_PATH.read_text(encoding="utf-8"))) == srt_text

    written_srt = write_srt_from_story(STORY_PATH, GENERATED_SRT)
    assert GENERATED_SRT.exists(), GENERATED_SRT
    assert parse_srt(GENERATED_SRT.read_text(encoding="utf-8")) == parse_srt(written_srt)

    try:
        build_srt_from_story({"version": "1.0", "title": "Empty", "segments": []})
        raise AssertionError("expected ValueError for empty story")
    except ValueError as exc:
        assert "expected at least 1 segment" in str(exc), exc

    missing_story = TEST_ROOT / "missing-story.json"
    try:
        write_srt_from_story(missing_story, TEST_ROOT / "missing.srt")
        raise AssertionError("expected FileNotFoundError for missing story")
    except FileNotFoundError:
        pass

    main_cli([
        str(STORY_PATH),
        "--output",
        str(OUTPUT_DIR),
        "--image",
        str(IMAGE_PATH),
        "--renderer",
        "whiteboard",
        "--tts",
        "edge",
        "--srt-output",
        str(OUTPUT_DIR / "story.srt"),
    ])

    final_video = OUTPUT_DIR / "rocket-final.mp4"
    assert (OUTPUT_DIR / "story.srt").exists(), OUTPUT_DIR
    assert final_video.exists() and final_video.stat().st_size > 0, final_video

    info = _ffprobe(final_video)
    assert info["codec_name"] == "h264", info
    assert info["width"] == "1080", info
    assert info["height"] == "1080", info
    assert float(info["duration"]) > 0, info

    report = {
        "story": str(STORY_PATH),
        "generated_srt": str(OUTPUT_DIR / "story.srt"),
        "image": str(IMAGE_PATH),
        "output": str(final_video),
        "renderer": "whiteboard",
        "codec": info["codec_name"],
        "resolution": f"{info['width']}x{info['height']}",
        "duration_sec": round(float(info["duration"]), 3),
        "elapsed_sec": round(time.time() - SCRIPT_START, 3),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"generated_srt={GENERATED_SRT}")
    print(f"pipeline_srt={OUTPUT_DIR / 'story.srt'}")
    print(f"final_video={final_video}")
    print(f"duration={float(info['duration']):.3f}")
    print(f"report={REPORT}")
    print(f"elapsed_sec={time.time() - SCRIPT_START:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
