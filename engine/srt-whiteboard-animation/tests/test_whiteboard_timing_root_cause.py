"""M4-4 白板渲染时长根因修复的定向测试（T1–T8）。

覆盖点：
- T1 正常目标时长：编排不超出预算（目标 - 凝视尾巴）。
- T2 旧 annotation 时长过长：本次目标时长生效，源 annotation 字节不变。
- T3 短目标时长：预算不合理时不静默丢弃笔画，输出可诊断警告。
- T4 事件边界：无负时间、无负持续时间、事件顺序不乱、结束不超预算。
- T5 确定性：同输入同目标重复处理得到一致结果。
- T6 旧调用兼容：未启用适配的旧调用行为不变。
- T7 真实渲染时长：合成 annotation + 测试图，真实渲染并用 ffprobe 测量，
  再跑 fit_clip_duration 观察后处理是否被触发。
- T8 既有契约回归：跑 M4-1 / M4-2 定向测试脚本，确保未退化。

运行方式（仅使用指定虚拟环境，不联网、不安装依赖、不调用 TTS）：
    cd /home/hqb/projects/video-factory/engine/srt-whiteboard-animation
    PYTHONPATH=scripts .venv/bin/python tests/test_whiteboard_timing_root_cause.py

边界：所有产物写入 /tmp/phase32-m4-4-20261026/tests/，不触碰仓库输出目录；
不读取 .gitignore*、.env、PROJECT_PLAN.md。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

ENGINE = Path(__file__).resolve().parent.parent
SCRIPTS = ENGINE / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

WORK_ROOT = Path(os.environ.get("M44_WORK_ROOT", "/tmp/phase32-m4-4-20261026/tests"))

import renderers.whiteboard as wb_module  # noqa: E402
from renderers.whiteboard import GAZE_TAIL_MS, WhiteboardRenderer  # noqa: E402
from scene.model import Scene  # noqa: E402
import video_pipeline as vp  # noqa: E402

EVIDENCE: dict[str, dict] = {}


# ---------------------------------------------------------------- helpers
def make_image(path: Path, count: int = 8) -> Path:
    canvas = np.full((720, 720, 3), 255, dtype=np.uint8)
    cols = max(1, min(4, count))
    for i in range(count):
        x = 20 + (i % cols) * 170
        y = 20 + (i // cols) * 110
        if i % 2 == 0:
            cv2.rectangle(canvas, (x, y), (x + 130, y + 80), (0, 0, 0), 5)
        else:
            cv2.circle(canvas, (x + 65, y + 40), 38, (0, 0, 0), 5)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), canvas)
    return path


def write_annotation(path: Path, count: int, step: int, dur: int, declared_ms: int) -> Path:
    elements = []
    for i in range(count):
        x = 20 + (i % 4) * 170
        y = 20 + (i // 4) * 110
        elements.append(
            {
                "id": f"part-{i + 1}",
                "label": f"区域 {i + 1}",
                "sequence": i + 1,
                "type": "structure",
                "region": {"x": x, "y": y, "width": 150, "height": 90},
                "reveal": {
                    "direction": "top_to_bottom",
                    "startMs": 200 + i * step,
                    "durationMs": dur,
                    "maskPaddingPx": 12,
                    "protectedRegions": [],
                },
                "handPath": {"start": [40, 40], "end": [180, 120], "easing": "easeInOut"},
            }
        )
    payload = {
        "sceneId": path.stem,
        "canvas": {"width": 720, "height": 720},
        "sceneDurationMs": declared_ms,
        "elements": elements,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def make_scene(image: Path, annotation: Path, duration_ms: int, sid: str) -> Scene:
    return Scene(
        id=sid,
        renderer="whiteboard",
        visual={"image": str(image), "annotation": str(annotation)},
        subtitles=[{"id": 1, "start": "00:00:00,000", "end": "00:00:05,000", "text": "t"}],
        duration_ms=duration_ms,
    )


def ffprobe_ms(path: Path) -> int:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return int(round(float(out.splitlines()[0]) * 1000))


def record_argv(monkeypatch_target):
    """拦截 _rsw.main，记录 argv，不真正渲染。"""
    calls: list[list[str]] = []
    original = monkeypatch_target

    def fake_main(argv):
        calls.append([str(item) for item in argv])
        return None

    return calls, fake_main, original


def arg_value(argv, flag):
    if flag in argv:
        return argv[argv.index(flag) + 1]
    return None


def frame_ms(fps: int = 60) -> float:
    return 1000.0 / fps


# ------------------------------------------------------------------- T1
def test_t1_normal_target_budget():
    case = WORK_ROOT / "t1"
    image = make_image(case / "canvas.png")
    ann = write_annotation(case / "canvas.annotation.json", 8, 500, 700, 12000)
    scene = make_scene(image, ann, 3400, "t1")

    calls, fake_main, original = record_argv(wb_module._rsw.main)
    wb_module._rsw.main = fake_main
    renderer = WhiteboardRenderer(fit_scene_duration=True)
    try:
        renderer.render(scene, case / "t1.mp4")
    finally:
        wb_module._rsw.main = original

    assert len(calls) == 1, f"expect 1 render call, got {len(calls)}"
    argv = calls[0]
    assert arg_value(argv, "--total-ms") == "3400", argv
    data = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    assert data["sceneDurationMs"] == 3400, data["sceneDurationMs"]
    max_end = max(
        float(e["reveal"]["startMs"]) + float(e["reveal"]["durationMs"]) for e in data["elements"]
    )
    budget_ms = 3400 - GAZE_TAIL_MS
    assert max_end <= budget_ms + 1e-6, f"plan end {max_end}ms exceeds budget {budget_ms}ms"

    plan = renderer.last_timing_plan
    assert plan is not None
    assert plan["planned_ms"] <= plan["budget_ms"], plan
    EVIDENCE["T1"] = {"target": 3400, "plan_end_ms": round(max_end, 3),
                      "planned_ms": plan["planned_ms"], "budget_ms": plan["budget_ms"],
                      "mode": plan["mode"]}
    print(f"[ok] T1 plan_end={max_end:.3f}ms <= budget={budget_ms}ms mode={plan['mode']}")


# ------------------------------------------------------------------- T2
def test_t2_stale_annotation_does_not_override():
    case = WORK_ROOT / "t2"
    image = make_image(case / "canvas.png")
    ann = write_annotation(case / "canvas.annotation.json", 8, 500, 700, 40000)
    original_bytes = ann.read_bytes()
    scene = make_scene(image, ann, 3400, "t2")

    calls, fake_main, original = record_argv(wb_module._rsw.main)
    wb_module._rsw.main = fake_main
    try:
        WhiteboardRenderer(fit_scene_duration=True).render(scene, case / "t2.mp4")
    finally:
        wb_module._rsw.main = original

    argv = calls[0]
    assert arg_value(argv, "--total-ms") == "3400", argv
    forwarded = Path(argv[1])
    assert forwarded != ann, "stale annotation must not be reused as-is"
    data = json.loads(forwarded.read_text(encoding="utf-8"))
    assert data["sceneDurationMs"] == 3400, data["sceneDurationMs"]
    assert ann.read_bytes() == original_bytes, "source annotation must stay byte-identical"
    EVIDENCE["T2"] = {"declared_ms": 40000, "target_ms": 3400,
                      "forwarded_annotation": str(forwarded), "source_untouched": True}
    print("[ok] T2 stale sceneDurationMs=40000ms overridden by target=3400ms, source untouched")


# ------------------------------------------------------------------- T3
def test_t3_short_target_reports_infeasible():
    case = WORK_ROOT / "t3"
    image = make_image(case / "canvas.png")
    ann = write_annotation(case / "canvas.annotation.json", 8, 500, 700, 12000)
    scene = make_scene(image, ann, 400, "t3")

    calls, fake_main, original = record_argv(wb_module._rsw.main)
    wb_module._rsw.main = fake_main
    renderer = WhiteboardRenderer(fit_scene_duration=True)
    try:
        renderer.render(scene, case / "t3.mp4")
    finally:
        wb_module._rsw.main = original

    data = json.loads(Path(calls[0][1]).read_text(encoding="utf-8"))
    assert len(data["elements"]) == 8, "strokes must not be dropped silently"
    plan = renderer.last_timing_plan
    assert plan["budget_insufficient"] is True, plan
    assert plan["warnings"], plan
    assert plan["planned_ms"] > plan["budget_ms"], plan
    EVIDENCE["T3"] = {"target_ms": 400, "elements_kept": len(data["elements"]),
                      "budget_ms": plan["budget_ms"], "planned_ms": plan["planned_ms"],
                      "budget_insufficient": plan["budget_insufficient"],
                      "warnings": plan["warnings"]}
    print(f"[ok] T3 infeasible budget reported, 8 strokes kept, "
          f"planned={plan['planned_ms']}ms > budget={plan['budget_ms']}ms")


# ------------------------------------------------------------------- T4
def test_t4_event_boundaries():
    case = WORK_ROOT / "t4"
    image = make_image(case / "canvas.png")
    ann = write_annotation(case / "canvas.annotation.json", 24, 180, 420, 30000)
    scene = make_scene(image, ann, 3400, "t4")

    calls, fake_main, original = record_argv(wb_module._rsw.main)
    wb_module._rsw.main = fake_main
    renderer = WhiteboardRenderer(fit_scene_duration=True)
    try:
        renderer.render(scene, case / "t4.mp4")
    finally:
        wb_module._rsw.main = original

    data = json.loads(Path(calls[0][1]).read_text(encoding="utf-8"))
    budget_ms = 3400 - GAZE_TAIL_MS
    prev_start = -1.0
    for element in data["elements"]:
        reveal = element["reveal"]
        start = float(reveal["startMs"])
        duration = float(reveal["durationMs"])
        assert start >= 0, f"negative start: {start}"
        assert duration > 0, f"non-positive duration: {duration}"
        assert start + duration <= budget_ms + 1e-6, (
            f"{element['id']} end={start + duration} exceeds budget={budget_ms}"
        )
        assert start >= prev_start - 1e-9, f"event order broken at {element['id']}"
        prev_start = start
    sampled = data["elements"][:3]
    EVIDENCE["T4"] = {"elements": len(data["elements"]), "budget_ms": budget_ms,
                      "sample": [(e["reveal"]["startMs"], e["reveal"]["durationMs"]) for e in sampled]}
    print(f"[ok] T4 {len(data['elements'])} events within [0, {budget_ms}]ms, order preserved")


# ------------------------------------------------------------------- T5
def test_t5_deterministic():
    case = WORK_ROOT / "t5"
    image = make_image(case / "canvas.png")
    ann = write_annotation(case / "canvas.annotation.json", 10, 400, 650, 12000)

    renderer_a = WhiteboardRenderer(fit_scene_duration=True)
    out_a = renderer_a._fit_annotation(ann, 3400, case / "run-a.mp4")
    plan_a = dict(renderer_a.last_timing_plan)

    renderer_b = WhiteboardRenderer(fit_scene_duration=True)
    out_b = renderer_b._fit_annotation(ann, 3400, case / "run-a.mp4")
    plan_b = dict(renderer_b.last_timing_plan)

    assert out_a == out_b, (out_a, out_b)
    assert out_a.read_bytes() == out_b.read_bytes(), "same input must yield identical plan"
    assert plan_a == plan_b, (plan_a, plan_b)
    EVIDENCE["T5"] = {"artifact": str(out_a), "mode": plan_a["mode"],
                      "planned_ms": plan_a["planned_ms"], "identical": True}
    print(f"[ok] T5 repeated processing identical -> {out_a.name}")


# ------------------------------------------------------------------- T6
def test_t6_legacy_call_unchanged():
    case = WORK_ROOT / "t6"
    image = make_image(case / "canvas.png")
    ann = write_annotation(case / "canvas.annotation.json", 8, 500, 700, 9000)
    scene = make_scene(image, ann, 3400, "t6")

    calls, fake_main, original = record_argv(wb_module._rsw.main)
    wb_module._rsw.main = fake_main
    try:
        WhiteboardRenderer().render(scene, case / "legacy.mp4")
        WhiteboardRenderer(total_ms=3000).render(scene, case / "explicit.mp4")
    finally:
        wb_module._rsw.main = original

    legacy_argv, explicit_argv = calls
    assert arg_value(legacy_argv, "--total-ms") is None, legacy_argv
    assert Path(legacy_argv[1]) == ann, "legacy call must use the original annotation"
    assert arg_value(explicit_argv, "--total-ms") == "3000", explicit_argv
    assert Path(explicit_argv[1]) == ann, "explicit total_ms must not rewrite annotation"
    assert WhiteboardRenderer(fit_scene_duration=True).resolve_target_ms(scene) == 3400
    assert WhiteboardRenderer().resolve_target_ms(scene) is None
    assert WhiteboardRenderer(total_ms=1234, fit_scene_duration=True).resolve_target_ms(scene) == 1234
    assert not list(case.glob("*.fit*.annotation.json")), "legacy call must not emit timing artifact"
    EVIDENCE["T6"] = {"legacy_total_ms": None, "explicit_total_ms": 3000,
                      "legacy_annotation_unchanged": True}
    print("[ok] T6 legacy / explicit-total_ms calls keep旧行为")


# ------------------------------------------------------------------- T7
def test_t7_real_render_duration():
    rows = []
    for target in (2000, 3400):
        case = WORK_ROOT / f"t7-{target}"
        image = make_image(case / "canvas.png")
        ann = write_annotation(case / "canvas.annotation.json", 8, 500, 700, 12000)
        scene = make_scene(image, ann, target, f"t7-{target}")
        clip = case / f"t7-{target}.mp4"
        renderer = WhiteboardRenderer(fit_scene_duration=True, cap_long_edge=540)
        renderer.render(scene, clip)
        raw_ms = ffprobe_ms(clip)
        record = vp.fit_clip_duration(clip, target, label=f"t7-{target}")
        post_ms = record.get("adjusted_ms") or ffprobe_ms(clip)
        plan = renderer.last_timing_plan
        assert abs(raw_ms - target) <= 2 * frame_ms(), (
            f"renderer raw duration {raw_ms}ms deviates from target {target}ms"
        )
        assert record["mode"] == "none", f"post-processing still needed: {record}"
        rows.append({
            "target_ms": target,
            "raw_ms": raw_ms,
            "post_ms": post_ms,
            "fit_mode": record["mode"],
            "speed_factor": record.get("speed_factor"),
            "final_error_ms": post_ms - target,
            "plan_mode": plan["mode"],
            "planned_ms": plan["planned_ms"],
        })
    EVIDENCE["T7"] = rows
    for row in rows:
        print(f"[ok] T7 target={row['target_ms']}ms raw={row['raw_ms']}ms "
              f"post={row['post_ms']}ms fit={row['fit_mode']} err={row['final_error_ms']}ms")


# ------------------------------------------------------------------- T8
def _run_test_script(script: Path, log_name: str) -> tuple[int, str]:
    log = WORK_ROOT / log_name
    log.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SCRIPTS)
    proc = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True, text=True, env=env, cwd=str(ENGINE), timeout=1800,
    )
    log.write_text(proc.stdout + "\n" + proc.stderr, encoding="utf-8")
    return proc.returncode, str(log)


def test_t8_existing_contracts():
    results = {}
    for name, script in (
        ("M4-1 duration contract", ENGINE / "tests" / "test_whiteboard_duration_contract.py"),
        ("M4-2 audio/video contract", ENGINE / "tests" / "test_audio_video_duration_contract.py"),
    ):
        code, log = _run_test_script(script, f"t8-{script.stem}.log")
        results[name] = {"exit": code, "log": log}
        assert code == 0, f"{name} regression failed, see {log}"
    EVIDENCE["T8"] = results
    print(f"[ok] T8 regression: {', '.join(f'{k} exit=0' for k in results)}")


def main() -> int:
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    print(f"[info] work root: {WORK_ROOT}")
    test_t1_normal_target_budget()
    test_t2_stale_annotation_does_not_override()
    test_t3_short_target_reports_infeasible()
    test_t4_event_boundaries()
    test_t5_deterministic()
    test_t6_legacy_call_unchanged()
    test_t7_real_render_duration()
    if os.environ.get("M44_SKIP_T8") != "1":
        test_t8_existing_contracts()
    else:
        print("[skip] T8 skipped by M44_SKIP_T8=1")
    (WORK_ROOT / "evidence.json").write_text(
        json.dumps(EVIDENCE, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("[ok] all M4-4 timing root-cause tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
