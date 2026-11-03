"""Phase 2 test: cinematic high-level visual MVP.

Purpose:
    Verify the phase-2 cinematic mode additions without breaking phase-1
    behavior:
    1. create_renderer("cinematic") returns a working full-screen background +
       Ken Burns + title renderer (real render + ffprobe).
    2. resolve_visual_representation honors renderer_preference and
       explicit_image (Asset Resolver reuse path).
    3. build_final_video applies force_style (unified Chinese subtitle style)
       and still has no -shortest.
    4. main() in cinematic mode with a background map produces one clip per
       scene (3 scenes) with renderer=cinematic (mocked TTS/audio/final).

No network is required: TTS and audio merge are mocked; backgrounds are
generated locally with PIL.

Usage:
    PYTHONPATH=scripts .venv/bin/python tests/test_cinematic_renderer.py
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw

from renderers.router import create_renderer
from scene.model import Scene
from scene.visual_representation import resolve_visual_representation
import video_pipeline
from video_pipeline import build_final_video, main as run_pipeline

SCRIPT_START = time.time()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_OUTPUT = PROJECT_ROOT / "output" / "phase2-cinematic-test"

SAMPLE_SRT = """1
00:00:00,000 --> 00:00:02,800
秦始皇下令连接北方各段城墙。

2
00:00:03,050 --> 00:00:06,450
工匠在陡峭的山脊上搬运巨大的条石。

3
00:00:06,700 --> 00:00:09,900
士兵在烽火台上点燃狼烟传递信号。

4
00:00:10,150 --> 00:00:13,350
明代长城用青砖砌筑出高大的墙体。

5
00:00:13,600 --> 00:00:17,200
蜿蜒的城墙像巨龙横卧在崇山峻岭之间。

6
00:00:17,450 --> 00:00:20,250
游客在斑驳的敌楼下驻足远眺。
"""


def _make_backgrounds() -> list[Path]:
    """Generate three local 1280x720 gradient backgrounds (PIL)."""
    bg_dir = TEST_OUTPUT / "backgrounds"
    bg_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    colors = [(60, 90, 130), (110, 80, 50), (40, 110, 90)]
    for i, (r, g, b) in enumerate(colors, start=1):
        img = Image.new("RGB", (1280, 720))
        draw = ImageDraw.Draw(img)
        for y in range(720):
            t = y / 719
            draw.line(
                [(0, y), (1280, y)],
                fill=(int(r * (1 - t) + 20 * t), int(g * (1 - t) + 20 * t), int(b * (1 - t) + 30 * t)),
            )
        p = bg_dir / f"bg-{i:02d}.png"
        img.save(p)
        paths.append(p)
    return paths


def test_router_supports_cinematic() -> None:
    renderer = create_renderer("cinematic")
    assert renderer.__class__.__name__ == "CinematicRenderer", renderer.__class__.__name__
    print("[ok] create_renderer('cinematic') -> CinematicRenderer")


def test_cinematic_single_scene_render() -> None:
    TEST_OUTPUT.mkdir(parents=True, exist_ok=True)
    bgs = _make_backgrounds()
    scene = Scene(
        id="scene-01",
        renderer="cinematic",
        visual={
            "image": str(bgs[0]),
            "subject": "mountain",
            "title": "长城烽火",
            "annotation": "秦始皇下令连接北方各段城墙。",
        },
        narration="秦始皇下令连接北方各段城墙。",
        subtitles=[{"id": 1, "start": "00:00:00,000", "end": "00:00:03,000", "text": "秦始皇下令连接北方各段城墙。", "start_ms": 0, "end_ms": 3000}],
        duration_ms=3000,
    )
    renderer = create_renderer("cinematic")
    out = TEST_OUTPUT / "scene-test.mp4"
    renderer.render(scene, out)
    assert out.exists() and out.stat().st_size > 0, "scene clip missing"
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-show_entries", "stream=width,height",
         "-of", "default=noprint_wrappers=1", str(out)],
        capture_output=True, text=True,
    )
    text = probe.stdout
    assert "1280" in text and "720" in text, f"expected 1280x720, got:\n{text}"
    print(f"[ok] cinematic single scene rendered 1280x720 ({out.stat().st_size} bytes)")


def test_resolve_visual_representation_preference() -> None:
    bgs = _make_backgrounds()
    scene = Scene(
        id="scene-01",
        renderer="cinematic",
        visual={"subject": "mountain", "title": "长城"},
        narration="测试旁白。",
        subtitles=[],
        duration_ms=1000,
    )
    rep = resolve_visual_representation(
        scene,
        asset_roots=[],
        renderer_preference="cinematic",
        explicit_image=str(bgs[0]),
    )
    assert rep.kind in ("explicit_image", "local_asset"), rep.kind
    assert rep.renderer == "cinematic", rep.renderer
    assert rep.payload.get("image") == str(bgs[0]), rep.payload
    print(f"[ok] resolve_visual_representation prefers cinematic renderer + explicit image ({rep.kind})")


def test_build_final_video_force_style() -> None:
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
            force_style="FontName=Noto Sans CJK SC,FontSize=20,PrimaryColour=&H00FFFFFF",
        )
    finally:
        video_pipeline.run = original_run

    assert captured, "expected build_final_video to invoke ffmpeg"
    cmd = captured[-1]
    assert any("force_style" in c for c in cmd), f"force_style missing: {cmd}"
    assert any("Noto Sans CJK SC" in c for c in cmd), "unified Chinese subtitle style missing"
    assert "-shortest" not in cmd, "-shortest must stay removed"
    print("[ok] build_final_video applies force_style Chinese subtitle style, no -shortest")


def test_main_cinematic_3scenes() -> None:
    TEST_OUTPUT.mkdir(parents=True, exist_ok=True)
    srt = TEST_OUTPUT / "sample.srt"
    srt.write_text(SAMPLE_SRT, encoding="utf-8")
    bgs = _make_backgrounds()
    background_map = {
        "scene-01": str(bgs[0]),
        "scene-02": str(bgs[1]),
        "scene-03": str(bgs[2]),
    }
    map_file = TEST_OUTPUT / "background_map.json"
    map_file.write_text(json.dumps(background_map), encoding="utf-8")

    def fake_generate_all_tts(provider, subtitles, tts_dir):
        tts_dir.mkdir(parents=True, exist_ok=True)
        fake_files = []
        for subtitle in subtitles:
            mp3 = tts_dir / f"{subtitle['id']:03d}.mp3"
            mp3.write_bytes(b"FAKE")
            fake_files.append({"id": subtitle["id"], "start": subtitle["start"], "end": subtitle["end"],
                               "text": subtitle["text"], "file": mp3})
        return fake_files

    def fake_build_audio_track(audio_files, output_audio):
        output_audio.write_bytes(b"FAKE-AUDIO")

    def fake_build_final_video(video, audio, srt, output, force_style=None):
        output.write_bytes(b"FAKE-FINAL")
        assert force_style is not None and "Noto Sans CJK SC" in force_style, f"force_style missing: {force_style}"

    originals = (
        video_pipeline.generate_all_tts,
        video_pipeline.build_audio_track,
        video_pipeline.build_final_video,
    )
    video_pipeline.generate_all_tts = fake_generate_all_tts
    video_pipeline.build_audio_track = fake_build_audio_track
    video_pipeline.build_final_video = fake_build_final_video
    try:
        run_pipeline(
            str(srt),
            str(TEST_OUTPUT / "run"),
            renderer_name="cinematic",
            tts_name="edge",
            background_map=background_map,
            group_target_ms=6000,
        )
    finally:
        video_pipeline.generate_all_tts, video_pipeline.build_audio_track, video_pipeline.build_final_video = originals

    scene_json = TEST_OUTPUT / "run" / "scene.json"
    assert scene_json.exists(), "scene.json missing"
    payload = json.loads(scene_json.read_text(encoding="utf-8"))
    assert isinstance(payload, list) and len(payload) == 3, f"expected 3 scenes, got {len(payload)}"
    assert all(scene.get("renderer") == "cinematic" for scene in payload), "renderer must be cinematic"
    render_mp4 = TEST_OUTPUT / "run" / "run-render.mp4"
    assert render_mp4.exists() and render_mp4.stat().st_size > 0, "merged render.mp4 missing"
    clips = sorted((TEST_OUTPUT / "run").glob("run-scene-*.mp4"))
    assert len(clips) == 3, f"expected 3 scene clips, got {len(clips)}"
    print(f"[ok] cinematic pipeline keeps 3 scenes, {len(clips)} clips, merged render.mp4")


def test_fade_filter_keeps_short_boundaries() -> None:
    renderer = create_renderer("cinematic")
    assert renderer._fade_filter(3.0, 0.35) == (
        "fade=t=in:st=0:d=0.350:alpha=0:color=black,"
        "fade=t=out:st=2.650:d=0.350:alpha=0:color=black,"
    )
    # fade must never exceed half the scene duration
    assert renderer._fade_filter(0.5, 0.35) == (
        "fade=t=in:st=0:d=0.250:alpha=0:color=black,"
        "fade=t=out:st=0.250:d=0.250:alpha=0:color=black,"
    )
    print("[ok] cinematic fade filter keeps short boundaries")


def _load_zoompan_expr():
    """Load cinematic.py without importing the full renderers package."""
    scripts_dir = PROJECT_ROOT / "scripts"
    renderers_dir = scripts_dir / "renderers"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    cinematic_path = renderers_dir / "cinematic.py"
    spec = importlib.util.spec_from_file_location("cinematic_zoompan_isolation", cinematic_path)
    assert spec and spec.loader, f"cannot load {cinematic_path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._zoompan_expr


def _run_zoompan_smoke_check(filtergraph: str) -> None:
    """Ask the installed FFmpeg to parse/run a generated zoompan expression."""
    cmd = [
        "ffmpeg", "-v", "error", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=red:s=32x32:d=0.2:r=25",
        "-frames:v", "5", "-vf", filtergraph, "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "ffmpeg returned non-zero").strip()
        raise AssertionError(f"FFmpeg rejected zoompan filtergraph: {filtergraph}\n{detail}")


def test_zoompan_styles_parse_with_installed_ffmpeg() -> None:
    from renderers.cinematic import _zoompan_expr

    # `time` is valid in zoompan; the high-resolution 4:4:4 stage lets integer
    # source crop steps survive as subpixel motion after downscaling.
    for style in ("zoom-in", "zoom-out", "pan-left", "pan-right"):
        expr = _zoompan_expr(style, 125, output_width=128, output_height=72)
        assert "time" in expr, expr
        assert "t/" not in expr, expr
        assert "s=128x72:fps=25" in expr, expr
        filtergraph = f"scale=192:108,{expr},format=yuv444p,scale=64:36:flags=lanczos,format=yuv420p"
        _run_zoompan_smoke_check(filtergraph)
    assert "time/5.000000" in _zoompan_expr("pan-right", 125)
    print("[ok] cinematic zoompan styles parse and run with installed FFmpeg")


def main() -> int:
    test_router_supports_cinematic()
    test_cinematic_single_scene_render()
    test_resolve_visual_representation_preference()
    test_build_final_video_force_style()
    test_main_cinematic_3scenes()
    test_zoompan_styles_parse_with_installed_ffmpeg()
    print(f"elapsed_sec={time.time() - SCRIPT_START:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
