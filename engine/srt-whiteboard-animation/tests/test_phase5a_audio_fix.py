"""Phase 5A-3F-4 audio regression test.

Purpose:
- Verify build_audio_track() uses a PCM-based intermediate path that preserves
  speech continuity for multi-input delayed audio mixing.
- Keep the regression deterministic and local (no network, no TTS provider).

Core logic:
1. Generate synthetic sine WAV files with ffmpeg.
2. Build a real M4A track through build_audio_track().
3. Inspect duration, silencedetect windows, and first-input continuity.
4. Check temp-dir cleanup after success and failure.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from video_pipeline import build_audio_track


SCRIPT_START = time.time()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output" / "phase5a-audio-fix-preview"
REPORT_PATH = OUTPUT_DIR / "regression-report.json"


def _ffmpeg_sine(path: Path, duration_s: float, freq: int) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={freq}:sample_rate=48000",
            "-t",
            f"{duration_s:.3f}",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _ffprobe_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
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
    return float(payload["format"]["duration"])


def _non_silent_windows(path: Path) -> list[dict[str, float]]:
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "info",
            "-i",
            str(path),
            "-af",
            "silencedetect=n=-50dB:d=0.05",
            "-f",
            "null",
            "-",
        ],
        check=True,
        capture_output=True,
        text=False,
    )
    stderr = result.stderr.decode("utf-8", errors="ignore")

    silence_starts: list[float] = []
    for line in stderr.splitlines():
        if "silence_start:" in line:
            silence_starts.append(float(line.split("silence_start:", 1)[1].strip()))

    duration = _ffprobe_duration(path)
    ranges: list[tuple[float, float]] = []
    cursor = 0.0
    for start in silence_starts:
        if start > cursor + 0.02:
            ranges.append((cursor, start))
        cursor = max(cursor, start)
    if cursor < duration - 0.02:
        ranges.append((cursor, duration))

    windows: list[dict[str, float]] = []
    for start, end in ranges:
        if end - start < 0.05:
            continue
        windows.append({"start": round(start, 3), "end": round(end, 3)})
    return windows


def _has_energy_near(path: Path, center_s: float, radius_s: float = 0.05) -> bool:
    for window in _non_silent_windows(path):
        if window["start"] <= center_s + radius_s and window["end"] >= center_s - radius_s:
            return True
    return False


def test_single_input_success(tmp: Path) -> None:
    audio_a = tmp / "single-input.wav"
    _ffmpeg_sine(audio_a, 2.0, 440)
    output_audio = tmp / "single-input.m4a"

    result = build_audio_track(
        [
            {
                "id": 1,
                "start": "00:00:00,000",
                "end": "00:00:02,000",
                "text": "single",
                "file": audio_a,
            }
        ],
        output_audio,
    )

    assert result == output_audio
    assert output_audio.exists()
    assert output_audio.stat().st_size > 0
    duration = _ffprobe_duration(output_audio)
    assert duration >= 1.9, f"single-input duration too short: {duration:.3f}"


def test_two_input_delay_and_tail(tmp: Path) -> None:
    audio_a = tmp / "two-input-a.wav"
    audio_b = tmp / "two-input-b.wav"
    _ffmpeg_sine(audio_a, 3.2, 440)
    _ffmpeg_sine(audio_b, 3.0, 660)

    output_audio = tmp / "two-input.m4a"
    build_audio_track(
        [
            {
                "id": 1,
                "start": "00:00:00,000",
                "end": "00:00:03,200",
                "text": "a",
                "file": audio_a,
            },
            {
                "id": 2,
                "start": "00:00:03,000",
                "end": "00:00:06,000",
                "text": "b",
                "file": audio_b,
            },
        ],
        output_audio,
    )

    duration = _ffprobe_duration(output_audio)
    assert duration >= 5.8, f"two-input duration should preserve tail: {duration:.3f}"
    assert _has_energy_near(output_audio, 2.0), "first-input energy should survive at ~2.0s"
    assert _has_energy_near(output_audio, 3.5), "second-input energy should appear near ~3.5s"


def test_cleanup_on_success_and_failure(tmp: Path) -> None:
    audio_a = tmp / "cleanup-a.wav"
    _ffmpeg_sine(audio_a, 1.0, 300)
    output_audio = tmp / "cleanup-out.m4a"

    build_audio_track(
        [
            {
                "id": 1,
                "start": "00:00:00,000",
                "end": "00:00:01,000",
                "text": "cleanup",
                "file": audio_a,
            }
        ],
        output_audio,
    )

    leftovers = list(tmp.glob(".audio-track-tmp-*"))
    assert leftovers == [], f"temp dirs leaked on success: {leftovers}"

    missing_audio = tmp / "missing.wav"
    try:
        build_audio_track(
            [
                {
                    "id": 1,
                    "start": "00:00:00,000",
                    "end": "00:00:01,000",
                    "text": "bad",
                    "file": missing_audio,
                }
            ],
            tmp / "cleanup-fail.m4a",
        )
        raise AssertionError("expected duration probe failure for missing input")
    except RuntimeError as exc:
        assert "missing.wav" in str(exc), str(exc)

    leftovers = list(tmp.glob(".audio-track-tmp-*"))
    assert leftovers == [], f"temp dirs leaked on failure: {leftovers}"


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = OUTPUT_DIR / "unit"
    tmp.mkdir(parents=True, exist_ok=True)

    test_single_input_success(tmp)
    test_two_input_delay_and_tail(tmp)
    test_cleanup_on_success_and_failure(tmp)

    report = {
        "test": "phase5a-audio-fix-regression",
        "elapsed_sec": round(time.time() - SCRIPT_START, 3),
        "output_dir": str(OUTPUT_DIR),
        "checks": [
            "single input preserves duration",
            "two-input delayed mix preserves tail energy",
            "temp dirs are cleaned on success and failure",
        ],
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] regression_report={REPORT_PATH}")
    print(f"elapsed_sec={time.time() - SCRIPT_START:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
