"""Phase 4A duration fix test: render duration must follow original SRT timeline."""

from __future__ import annotations

import time

from video_pipeline import plan_render_duration_ms
from scene.model import Scene


SCRIPT_START = time.time()

SRT_GAP_SCENES = [
    Scene(
        id="scene-01",
        narration="first",
        duration_ms=6450,
        subtitles=[
            {"id": 1, "start": "00:00:00,000", "end": "00:00:02,800", "text": "a", "start_ms": 0, "end_ms": 2800},
            {"id": 2, "start": "00:00:03,050", "end": "00:00:06,450", "text": "b", "start_ms": 3050, "end_ms": 6450},
        ],
    ),
    Scene(
        id="scene-02",
        narration="second",
        duration_ms=6850,
        subtitles=[
            {"id": 3, "start": "00:00:09,000", "end": "00:00:12,200", "text": "c", "start_ms": 9000, "end_ms": 12200},
            {"id": 4, "start": "00:00:12,450", "end": "00:00:15,850", "text": "d", "start_ms": 12450, "end_ms": 15850},
        ],
    ),
    Scene(
        id="scene-03",
        narration="third",
        duration_ms=6650,
        subtitles=[
            {"id": 5, "start": "00:00:18,500", "end": "00:00:22,100", "text": "e", "start_ms": 18500, "end_ms": 22100},
            {"id": 6, "start": "00:00:22,350", "end": "00:00:25,150", "text": "f", "start_ms": 22350, "end_ms": 25150},
        ],
    ),
]


def test_render_duration_spans_scene_gaps_and_final_end() -> None:
    durations = plan_render_duration_ms(SRT_GAP_SCENES, final_end_ms=26240)
    assert durations == [9000, 9500, 7740], durations
    print(f"[ok] phase4a render durations cover original timeline gaps: {durations}")


def test_render_duration_ignores_audio_overshoot() -> None:
    durations = plan_render_duration_ms(
        SRT_GAP_SCENES,
        final_end_ms=25150,
        audio_duration_ms=26238,
    )
    assert durations == [9000, 9500, 6650], durations
    print(f"[ok] phase4a render durations keep subtitle timeline independent of audio overshoot: {durations}")


def main() -> int:
    test_render_duration_spans_scene_gaps_and_final_end()
    test_render_duration_ignores_audio_overshoot()
    print(f"elapsed_sec={time.time() - SCRIPT_START:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
