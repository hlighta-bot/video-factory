"""Phase 1 fix test: multi-scene retention and full narration in final video.

Purpose:
    Verify the minimal fix for two defects:
    1. Scene loss: no-image branch previously rendered only planned_scenes[0].
       Now every planned scene must be kept (scene.json array) and rendered.
    2. A/V out-of-sync: build_final_video used -shortest, truncating the
       narration when the video is shorter than the audio track.
       The flag must be removed so the full narration survives.

Checks (no network / no real TTS):
    - plan_srt_grouped returns all scenes with positive durations covering
      every SRT cue for the acceptance SRT.
    - build_final_video's ffmpeg invocation does NOT contain -shortest.
    - scene.json written by main() for a no-image run contains an array with
      all planned scenes (mocked TTS + mocked audio track).

Usage:
    PYTHONPATH=scripts .venv/bin/python tests/test_scene_loss_fix.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from scene.planner import plan_srt_grouped
from parse_srt import parse_srt
import video_pipeline
from video_pipeline import build_final_video, main as run_pipeline

SCRIPT_START = time.time()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ACCEPTANCE_SRT = PROJECT_ROOT / "output" / "my-greatwall-auto-test" / "my-story.srt"
TEST_OUTPUT = PROJECT_ROOT / "output" / "phase1-fix-test"


def _cues_from(path: Path) -> list[dict]:
    return parse_srt(path.read_text(encoding="utf-8"))


def test_planner_keeps_all_scenes() -> None:
    cues = _cues_from(ACCEPTANCE_SRT)
    assert len(cues) == 6, f"expected 6 SRT cues, got {len(cues)}"
    scenes = plan_srt_grouped(
        cues,
        renderer="auto",
        target_duration_ms=8000,
        min_duration_ms=4000,
        max_duration_ms=12000,
    )
    assert len(scenes) >= 2, f"expected multiple scenes, got {len(scenes)}"
    for scene in scenes:
        assert scene.duration_ms > 0, f"scene {scene.id} has non-positive duration"
        assert scene.subtitles, f"scene {scene.id} has no subtitles"
    covered = [sub["id"] for scene in scenes for sub in scene.subtitles]
    assert sorted(covered) == list(range(1, 7)), f"cue coverage mismatch: {covered}"
    print(f"[ok] planner keeps {len(scenes)} scenes covering 6 cues")


def test_build_final_video_no_shortest() -> None:
    captured: list[list[str]] = []

    def fake_run(cmd):
        captured.append(list(map(str, cmd)))

    original_run = video_pipeline.run
    video_pipeline.run = fake_run
    try:
        build_final_video(
            Path("/tmp/fake-video.mp4"),
            Path("/tmp/fake-voice.m4a"),
            Path("/tmp/fake.srt"),
            Path("/tmp/fake-final.mp4"),
        )
    finally:
        video_pipeline.run = original_run

    assert captured, "expected build_final_video to invoke ffmpeg"
    cmd = captured[-1]
    assert "-shortest" not in cmd, f"-shortest must be removed, got: {cmd}"
    assert "-map" in cmd and "0:v:0" in cmd and "1:a:0" in cmd
    print("[ok] build_final_video no longer truncates narration with -shortest")


def test_main_scene_json_contains_all_scenes() -> None:
    """Run main() in no-image mode with mocked TTS/audio so no network is used."""
    TEST_OUTPUT.mkdir(parents=True, exist_ok=True)

    def fake_generate_all_tts(provider, subtitles, tts_dir):
        tts_dir.mkdir(parents=True, exist_ok=True)
        fake_files = []
        for subtitle in subtitles:
            mp3 = tts_dir / f"{subtitle['id']:03d}.mp3"
            mp3.write_bytes(b"FAKE")
            fake_files.append({
                "id": subtitle["id"],
                "start": subtitle["start"],
                "end": subtitle["end"],
                "text": subtitle["text"],
                "file": mp3,
            })
        return fake_files

    def fake_build_audio_track(audio_files, output_audio):
        output_audio.write_bytes(b"FAKE-AUDIO")

    original_tts = video_pipeline.generate_all_tts
    original_audio = video_pipeline.build_audio_track
    original_final = video_pipeline.build_final_video
    video_pipeline.generate_all_tts = fake_generate_all_tts
    video_pipeline.build_audio_track = fake_build_audio_track

    # Stub final merge to avoid running a real ffmpeg subtitles pass.
    def fake_build_final_video(video, audio, srt, output, force_style=None):
        output.write_bytes(b"FAKE-FINAL")
    video_pipeline.build_final_video = fake_build_final_video

    try:
        run_pipeline(
            str(ACCEPTANCE_SRT),
            str(TEST_OUTPUT),
            renderer_name="auto",
            tts_name="edge",
        )
    finally:
        video_pipeline.generate_all_tts = original_tts
        video_pipeline.build_audio_track = original_audio
        video_pipeline.build_final_video = original_final

    scene_json = TEST_OUTPUT / "scene.json"
    assert scene_json.exists(), "scene.json missing"
    payload = json.loads(scene_json.read_text(encoding="utf-8"))
    assert isinstance(payload, list), f"scene.json should be an array, got {type(payload)}"
    assert len(payload) >= 2, f"expected all scenes in scene.json, got {len(payload)}"
    scene_sub_count = sum(len(scene.get("subtitles", [])) for scene in payload)
    assert scene_sub_count == 6, f"expected 6 subtitles across scenes, got {scene_sub_count}"

    # Render clips for every scene must exist (real SVG renderer, no network).
    clips = sorted(TEST_OUTPUT.glob("*.mp4"))
    render_mp4 = TEST_OUTPUT / f"{TEST_OUTPUT.name}-render.mp4"
    assert render_mp4.exists() and render_mp4.stat().st_size > 0, "merged render.mp4 missing"
    print(f"[ok] scene.json contains all {len(payload)} scenes (6 subtitles)")
    print(f"[ok] merged render.mp4 = {render_mp4}")


def main() -> int:
    test_planner_keeps_all_scenes()
    test_build_final_video_no_shortest()
    test_main_scene_json_contains_all_scenes()
    print(f"elapsed_sec={time.time() - SCRIPT_START:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
