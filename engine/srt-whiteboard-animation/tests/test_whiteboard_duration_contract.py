"""M4-1 白板场景时长契约定向测试（T1–T4）。

覆盖点：
- T1 显式时长传递：流水线计算出的有效场景时长必须传到白板渲染入口（argv），
  并写入 annotation 的 sceneDurationMs，而不只是 Scene.duration_ms > 0。
- T2 旧 annotation 时长不覆盖新时长：annotation.sceneDurationMs 与本次有效时长不同、
  且书写编排超出新时长时，最终交给底层渲染器的是本次时长（原 annotation 不被修改）。
- T3 三场景时间轴：用项目真实 plan_srt_grouped + plan_render_duration_ms 计算，
  真实渲染三段场景视频，记录 SRT 起止 / 原始时长 / 有效时长 / 实际渲染时长 / 合并总时长。
- T4 兼容性：不传新参数（fit_scene_duration 默认 False）或仅传 total_ms 时行为不变。

约束：纯合成数据，不调用 TTS / LLM / Story Provider / 外部 API，不跑完整流水线。
所有媒体产物写入本次测试自建的临时目录。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent

import renderers.whiteboard as wb_module
from renderers.whiteboard import GAZE_TAIL_MS, WhiteboardRenderer
from scene.model import Scene
from scene.planner import plan_srt_grouped
from video_pipeline import plan_render_duration_ms

WORK_ROOT = Path(os.environ.get("M41_CONTRACT_TMP") or tempfile.mkdtemp(prefix="m41-contract-"))
WORK_ROOT.mkdir(parents=True, exist_ok=True)

_ORIGINAL_RSW_MAIN = wb_module._rsw.main


def _record_argv():
    """替换底层渲染入口，仅记录 argv，不做真实渲染。"""
    calls: list[list[str]] = []

    def fake_main(argv=None):
        calls.append(list(argv or []))
        return 0

    wb_module._rsw.main = fake_main
    return calls


def _restore():
    wb_module._rsw.main = _ORIGINAL_RSW_MAIN


def _arg_value(argv: list[str], flag: str):
    if flag not in argv:
        return None
    return argv[argv.index(flag) + 1]


def _write_annotation(path: Path, scene_duration_ms: int | None, elements: list[dict]) -> Path:
    payload = {
        "sceneId": path.stem,
        "canvas": {"width": 320, "height": 180},
        "elements": elements,
    }
    if scene_duration_ms is not None:
        payload["sceneDurationMs"] = scene_duration_ms
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _element(start_ms: int, duration_ms: int, bbox=(10, 10, 60, 60)) -> dict:
    x, y, w, h = bbox
    return {
        "id": f"el-{start_ms}",
        "bbox": {"x": x, "y": y, "width": w, "height": h},
        "reveal": {"startMs": start_ms, "durationMs": duration_ms},
        "label": "shape",
    }


def _make_scene(image: Path, annotation: Path, duration_ms: int, scene_id="scene-01") -> Scene:
    return Scene(
        id=scene_id,
        renderer="whiteboard",
        visual={"image": str(image), "annotation": str(annotation)},
        subtitles=[{"id": 1, "start": "00:00:00,000", "end": "00:00:04,000", "text": "test"}],
        duration_ms=duration_ms,
    )


def _make_image(path: Path) -> Path:
    canvas = np.full((180, 320, 3), 255, dtype=np.uint8)
    cv2.rectangle(canvas, (30, 30), (110, 110), (0, 0, 0), 6)
    cv2.circle(canvas, (200, 70), 40, (0, 0, 0), 6)
    cv2.line(canvas, (40, 150), (280, 150), (0, 0, 0), 8)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), canvas)
    return path


def _ffprobe_duration_ms(path: Path) -> int:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return int(round(float(out.splitlines()[0]) * 1000))


# ── T1 显式时长传递 ──
def test_t1_explicit_duration_is_forwarded():
    case_dir = WORK_ROOT / "t1"
    image = _make_image(case_dir / "canvas.png")
    annotation = _write_annotation(
        case_dir / "canvas.annotation.json",
        scene_duration_ms=9000,
        elements=[_element(300, 3000), _element(4000, 3000)],
    )
    scene = _make_scene(image, annotation, duration_ms=4200)
    calls = _record_argv()
    try:
        renderer = WhiteboardRenderer(fit_scene_duration=True)
        renderer.render(scene, case_dir / "t1-out.mp4")
    finally:
        _restore()

    assert len(calls) == 1, f"expect 1 render call, got {len(calls)}"
    argv = calls[0]
    forwarded = _arg_value(argv, "--total-ms")
    assert forwarded == "4200", f"--total-ms should be scene effective duration, got {forwarded}"

    forwarded_annotation = Path(argv[1])
    data = json.loads(forwarded_annotation.read_text(encoding="utf-8"))
    assert data["sceneDurationMs"] == 4200, data["sceneDurationMs"]
    max_end = max(
        int(e["reveal"]["startMs"]) + int(e["reveal"]["durationMs"]) for e in data["elements"]
    )
    assert max_end <= 4200 - GAZE_TAIL_MS, f"writing plan {max_end}ms exceeds budget"
    print(f"[ok] T1 --total-ms={forwarded}, annotation sceneDurationMs=4200, plan_end={max_end}ms")


# ── T2 旧 annotation 不覆盖新时长 ──
def test_t2_stale_annotation_does_not_override():
    case_dir = WORK_ROOT / "t2"
    image = _make_image(case_dir / "canvas.png")
    stale = _write_annotation(
        case_dir / "canvas.annotation.json",
        scene_duration_ms=20000,
        elements=[_element(300, 6000), _element(7000, 6000), _element(13500, 6000)],
    )
    original_bytes = stale.read_bytes()
    scene = _make_scene(image, stale, duration_ms=3200)

    calls = _record_argv()
    try:
        renderer = WhiteboardRenderer(fit_scene_duration=True)
        renderer.render(scene, case_dir / "t2-out.mp4")
    finally:
        _restore()

    argv = calls[0]
    assert _arg_value(argv, "--total-ms") == "3200", argv
    forwarded_annotation = Path(argv[1])
    assert forwarded_annotation != stale, "stale annotation must not be reused as-is"
    data = json.loads(forwarded_annotation.read_text(encoding="utf-8"))
    assert data["sceneDurationMs"] == 3200, data["sceneDurationMs"]
    max_end = max(
        int(e["reveal"]["startMs"]) + int(e["reveal"]["durationMs"]) for e in data["elements"]
    )
    assert max_end <= 3200 - GAZE_TAIL_MS, f"writing plan {max_end}ms exceeds budget"
    assert stale.read_bytes() == original_bytes, "original annotation must stay untouched"
    print(
        f"[ok] T2 stale sceneDurationMs=20000 ignored; forwarded=3200, plan_end={max_end}ms, "
        f"original annotation unchanged"
    )


# ── T3 三场景时间轴 ──
def test_t3_three_scene_timeline():
    case_dir = WORK_ROOT / "t3"
    cues = [
        {"index": 1, "startMs": 0, "endMs": 9000, "text": "第一段字幕"},
        {"index": 2, "startMs": 11000, "endMs": 20000, "text": "第二段字幕"},
        {"index": 3, "startMs": 22000, "endMs": 31000, "text": "第三段字幕"},
    ]
    scenes = plan_srt_grouped(cues, renderer="whiteboard", target_duration_ms=8000,
                              min_duration_ms=4000, max_duration_ms=12000)
    assert len(scenes) == 3, f"expect 3 planned scenes, got {len(scenes)}"
    final_end_ms = max(int(c["endMs"]) for c in cues)
    render_durations = plan_render_duration_ms(scenes, final_end_ms=final_end_ms)
    assert len(render_durations) == 3, render_durations

    fps = 15
    frame_ms = 1000.0 / fps
    rows = []
    clip_paths = []
    for index, (scene, render_ms) in enumerate(zip(scenes, render_durations), start=1):
        image = _make_image(case_dir / f"scene-{index:02d}.png")
        # 用过期时长生成 annotation，模拟旧资产被复用
        from auto_annotation import generate_annotation

        annotation = case_dir / f"scene-{index:02d}.annotation.json"
        generate_annotation(image, output_path=annotation, scene_id=f"scene-{index:02d}",
                            scene_duration_ms=max(9000, render_ms * 3))
        clip = case_dir / f"scene-{index:02d}.mp4"
        renderer = WhiteboardRenderer(fit_scene_duration=True, fps=fps, cap_long_edge=240)
        renderer.render(_make_scene(image, annotation, duration_ms=render_ms, scene_id=scene.id), clip)
        actual = _ffprobe_duration_ms(clip)
        error = actual - render_ms
        clip_paths.append(clip)
        rows.append({
            "scene": scene.id,
            "sub_start": scene.subtitles[0]["start"],
            "sub_end": scene.subtitles[-1]["end"],
            "raw_ms": scene.duration_ms,
            "render_ms": render_ms,
            "actual_ms": actual,
            "error_ms": error,
        })
        assert abs(error) <= frame_ms + 1, f"{scene.id} duration error {error}ms exceeds 1 frame"

    merged = case_dir / "merged.mp4"
    list_file = case_dir / "concat.txt"
    list_file.write_text("".join(f"file '{p}'\n" for p in clip_paths), encoding="utf-8")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
         "-i", str(list_file), "-c", "copy", str(merged)],
        check=True,
    )
    merged_ms = _ffprobe_duration_ms(merged)
    expected_total = sum(render_durations)

    print("[T3] scene | SRT start→end | raw_ms | render_ms | actual_ms | error_ms")
    for row in rows:
        print(
            f"     {row['scene']} | {row['sub_start']}→{row['sub_end']} | "
            f"{row['raw_ms']} | {row['render_ms']} | {row['actual_ms']} | {row['error_ms']:+d}"
        )
    print(f"[T3] merged actual={merged_ms}ms expected={expected_total}ms (SRT end={final_end_ms}ms)")
    assert abs(merged_ms - expected_total) <= len(rows) * frame_ms + 1, (
        f"merged duration {merged_ms}ms deviates from expected {expected_total}ms"
    )
    (WORK_ROOT / "t3-timeline.json").write_text(
        json.dumps({"fps": fps, "rows": rows, "merged_ms": merged_ms,
                    "expected_total_ms": expected_total, "srt_end_ms": final_end_ms},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return rows, merged_ms, expected_total


# ── T4 兼容性 ──
def test_t4_backward_compatibility():
    case_dir = WORK_ROOT / "t4"
    image = _make_image(case_dir / "canvas.png")
    annotation = _write_annotation(
        case_dir / "canvas.annotation.json",
        scene_duration_ms=9000,
        elements=[_element(300, 3000)],
    )
    scene = _make_scene(image, annotation, duration_ms=4200)

    calls = _record_argv()
    try:
        # 1) 旧调用方式：不传任何新参数
        WhiteboardRenderer().render(scene, case_dir / "legacy.mp4")
        # 2) 旧调用方式：只传 total_ms（既有测试与脚本的用法）
        WhiteboardRenderer(total_ms=3000).render(scene, case_dir / "explicit.mp4")
    finally:
        _restore()

    legacy_argv, explicit_argv = calls
    assert _arg_value(legacy_argv, "--total-ms") is None, legacy_argv
    assert Path(legacy_argv[1]) == annotation, "legacy call must use original annotation"
    assert _arg_value(explicit_argv, "--total-ms") == "3000", explicit_argv
    assert Path(explicit_argv[1]) == annotation, "explicit total_ms must not rewrite annotation"

    assert WhiteboardRenderer(fit_scene_duration=True).resolve_target_ms(scene) == 4200
    assert WhiteboardRenderer().resolve_target_ms(scene) is None
    assert WhiteboardRenderer(total_ms=1234, fit_scene_duration=True).resolve_target_ms(scene) == 1234
    print("[ok] T4 legacy call unchanged (no --total-ms, original annotation); "
          "explicit total_ms unchanged; opt-in resolves scene.duration_ms")


def main() -> int:
    print(f"[info] work root: {WORK_ROOT}")
    test_t1_explicit_duration_is_forwarded()
    test_t2_stale_annotation_does_not_override()
    test_t4_backward_compatibility()
    test_t3_three_scene_timeline()
    print("[ok] all M4-1 contract tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
