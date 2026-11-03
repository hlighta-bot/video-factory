"""M5-2R 剩余时间轴缺陷最小修复的针对性测试（T1–T6）。

覆盖点：
- T1 阻塞冲突：门禁必须在写出 timed SRT / 成品之前中断，失败语义明确；
      非阻塞警告（large_shift / capped / 非法窗口）不得被错误升级为失败。
- T2 单图分支时间基准：画面与音频/字幕共用最终时间轴，不再按原始 SRT 渲染。
- T3 尾部补齐：tpad 只在真实尾部容差内触发，且日志给出原因，不掩盖时间轴错误。
- T4 诊断字段：speech_overrun_ms（本条自身超窗）与 displacement_ms（前序位移）四象限不混淆。
- T5 边界输入：零时长 / 倒置区间 / 负起点 / 相邻 cue 边界 / 无法自动解决的约束。
- T6 回归：由 runner 单独执行既有测试文件（本文件不重复实现）。

约束：仅使用合成音频 / 合成视频 / 合成字幕与测试替身，不调用真实 TTS，不联网，
不访问历史媒体目录，不执行 Git 命令。所有产物写入 M52R_TMP 指定目录。

运行：
  PYTHONPATH=scripts .venv/bin/python tests/test_phase32_m5_2r_timeline_fix.py
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
for _path in (TESTS_DIR, PROJECT_ROOT / "scripts"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import video_pipeline as vp  # noqa: E402
import test_audio_video_duration_contract as base  # noqa: E402
from tts.base import TTSProvider  # noqa: E402

WORK_ROOT = Path(
    os.environ.get("M52R_TMP") or tempfile.mkdtemp(prefix="m52r-")
)
WORK_ROOT.mkdir(parents=True, exist_ok=True)

RESULTS: list[dict] = []
ROCKET_IMAGE = PROJECT_ROOT / "output" / "my-rocket" / "rocket.jpg"
FFMPEG = shutil.which("ffmpeg") or "ffmpeg"

# 单图分支真实运行的缓存（T2/T3 共享一次渲染，避免重复耗时）
_IMAGE_RUN: dict | None = None


# ============================================================
# 工具
# ============================================================

def _case_dir(name):
    path = WORK_ROOT / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _assert(condition, message):
    if not condition:
        raise AssertionError(message)


def _srt_time(ms):
    ms = max(0, int(round(ms)))
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _parse_srt_blocks(path):
    blocks = []
    for chunk in Path(path).read_text(encoding="utf-8").strip().split("\n\n"):
        lines = [line for line in chunk.splitlines() if line.strip()]
        if len(lines) < 3:
            continue
        span = lines[1]
        start_s, end_s = [part.strip() for part in span.split("-->")]
        blocks.append({
            "start_ms": _srt_ms(start_s),
            "end_ms": _srt_ms(end_s),
            "text": " ".join(lines[2:]).strip(),
        })
    return blocks


def _srt_ms(value):
    hours, minutes, rest = value.replace(",", ".").split(":")
    seconds = float(rest)
    return int(round((int(hours) * 3600 + int(minutes) * 60 + seconds) * 1000))


def _entries(rows):
    """rows: (id, planned_start_ms, planned_end_ms, duration_ms)"""
    return [
        {
            "id": cue_id,
            "planned_start_ms": start,
            "planned_end_ms": end,
            "duration_ms": duration,
            # build_final_timeline 的 overlaps_before 判定依赖实测结束点
            "actual_end_ms": start + duration,
        }
        for cue_id, start, end, duration in rows
    ]


def _kinds(timeline):
    return {
        (item["id"], item["kind"]): item.get("severity")
        for item in timeline["conflicts"]
    }


def _make_speech_file(path, seconds):
    base.make_speech(path, seconds)
    return path


class LocalSilentTTS(TTSProvider):
    """本地静音 TTS：不联网、不调用 Edge，仅用于流水线连通性验证。"""

    def __init__(self, duration_overrides: dict[int, float] | None = None):
        self.duration_overrides = duration_overrides or {}
        self.calls = 0

    def synthesize(self, text: str, output_path: Path) -> Path:
        self.calls += 1
        seconds = max(0.8, min(5.5, len(text) * 0.16))
        seconds += self.duration_overrides.get(self.calls, 0.0)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                FFMPEG, "-y", "-v", "error",
                "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                "-t", f"{seconds:.3f}", "-q:a", "9",
                str(output_path),
            ],
            check=True,
        )
        return output_path


def _inject_blocked_timeline(monkey_target):
    """故障注入：让时间轴返回一个明确的阻塞冲突（模拟不满足核心契约的输入）。"""

    def _fake(entries, **kwargs):
        timeline = monkey_target(entries, **kwargs)
        conflict = {
            "id": timeline["entries"][0]["id"],
            "kind": "subtitle_shorter_than_speech",
            "detail": "注入：字幕结束早于语音结束 1234ms",
            "severity": "blocking",
        }
        timeline["conflicts"] = list(timeline["conflicts"]) + [conflict]
        timeline["blocking_conflicts"] = [conflict]
        timeline["has_blocking_conflicts"] = True
        timeline["status"] = "conflict"
        return timeline

    return _fake


# ============================================================
# T1 阻塞冲突 / 非阻塞警告
# ============================================================

def t1a_real_blocking_shape():
    """真实可构造的阻塞形态：字幕被挤压到语音结束之前。"""
    state_dir = _case_dir("t1a")
    timeline = vp.build_final_timeline(
        _entries([
            (1, 0, 5000, 3000),
            (2, 1000, 1500, 500),
        ]),
        gap_ms=0,
    )
    kinds = _kinds(timeline)
    _assert(
        kinds.get((1, "subtitle_shorter_than_speech")) == "blocking",
        f"字幕早于语音消失必须判为 blocking，实际={kinds}",
    )
    _assert(
        kinds.get((1, "subtitle_window_capped")) == "warning",
        f"挤压本身应为 warning，实际={kinds}",
    )
    _assert(timeline["has_blocking_conflicts"] is True, "应标记存在阻塞冲突")
    _assert(timeline["status"] == "conflict", "状态应为 conflict")
    shorter = [
        item for item in timeline["entries"]
        if item["subtitle_end_ms"] < item["final_end_ms"]
    ]
    _assert(shorter, "应至少存在一条字幕早于语音结束的 cue")
    (state_dir / "t1a-blocking.json").write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    RESULTS.append({
        "case": "T1a",
        "blocking": [(c["id"], c["kind"]) for c in timeline["blocking_conflicts"]],
        "warnings": [(c["id"], c["kind"]) for c in timeline["warnings"]],
    })


def t1b_prepare_gate_refuses_to_write():
    """prepare_final_timeline 在阻塞冲突时不得写出 timed SRT / 覆盖已有文件。"""
    out = _case_dir("t1b") / "out"
    out.mkdir(parents=True, exist_ok=True)
    audio_a = _make_speech_file(_case_dir("t1b") / "a.wav", 1.2)
    audio_b = _make_speech_file(_case_dir("t1b") / "b.wav", 0.8)
    audio_files = [
        {"id": 1, "start": _srt_time(0), "end": _srt_time(2000), "file": audio_a},
        {"id": 2, "start": _srt_time(2200), "end": _srt_time(4000), "file": audio_b},
    ]
    subtitles = [
        {"id": 1, "start_ms": 0, "end_ms": 2000, "text": "第一条"},
        {"id": 2, "start_ms": 2200, "end_ms": 4000, "text": "第二条"},
    ]
    state = {}
    original = vp.build_final_timeline
    vp.build_final_timeline = _inject_blocked_timeline(original)
    raised = None
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            vp.prepare_final_timeline(out, audio_files, subtitles, state)
    except vp.TimelineContractError as exc:
        raised = exc
    finally:
        vp.build_final_timeline = original

    _assert(raised is not None, "阻塞冲突必须抛 TimelineContractError")
    _assert(isinstance(raised, RuntimeError), "异常应为 RuntimeError 子类，兼容既有失败路径")
    _assert(
        "subtitle_shorter_than_speech" in str(raised),
        f"诊断必须指出具体冲突类型，实际={raised}",
    )
    _assert(state.get("timeline_blocked") is True, "state 应标记 timeline_blocked")
    _assert(state.get("blocking_conflicts"), "state 应记录 blocking_conflicts")
    _assert(not (out / f"{out.name}-timed.srt").exists(), "阻塞冲突时不得写出 timed SRT")
    RESULTS.append({"case": "T1b", "error": type(raised).__name__, "blocked": True})


def t1c_warning_is_not_upgraded():
    """非阻塞冲突（large_shift / capped / 非法窗口）仍应完成生产并返回 warning。"""
    out = _case_dir("t1c") / "out"
    out.mkdir(parents=True, exist_ok=True)
    audio_a = _make_speech_file(_case_dir("t1c") / "a.wav", 8.0)
    audio_b = _make_speech_file(_case_dir("t1c") / "b.wav", 0.6)
    audio_files = [
        {"id": 1, "start": _srt_time(0), "end": _srt_time(1000), "file": audio_a},
        {"id": 2, "start": _srt_time(1200), "end": _srt_time(2000), "file": audio_b},
    ]
    subtitles = [
        {"id": 1, "start_ms": 0, "end_ms": 1000, "text": "长语音"},
        {"id": 2, "start_ms": 1200, "end_ms": 2000, "text": "短语音"},
    ]
    state = {}
    with contextlib.redirect_stdout(io.StringIO()):
        timeline, timed_srt = vp.prepare_final_timeline(out, audio_files, subtitles, state)

    _assert(timeline["status"] == "conflict", "存在警告时状态应为 conflict")
    _assert(not timeline["blocking_conflicts"], "纯位移/超窗不得判为阻塞")
    _assert(timed_srt is not None and Path(timed_srt).exists(), "警告不得阻断 timed SRT 产出")
    kinds = _kinds(timeline)
    _assert(
        kinds.get((2, "large_shift")) == "warning",
        f"位移应记为 warning，实际={kinds}",
    )
    RESULTS.append({
        "case": "T1c",
        "status": timeline["status"],
        "warnings": [(c["id"], c["kind"]) for c in timeline["warnings"]],
        "timed_srt": str(timed_srt),
    })


def t1d_pipeline_fails_without_artifacts():
    """全链路：阻塞冲突时抛错、manifest 记 failed、不产出成品。"""
    if not ROCKET_IMAGE.exists():
        raise RuntimeError(f"缺少测试图片，T1d UNVERIFIED: {ROCKET_IMAGE}")
    out = _case_dir("t1d") / "out"
    out.mkdir(parents=True, exist_ok=True)
    srt_path = _case_dir("t1d") / "input.srt"
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n第一条合成旁白\n\n"
        "2\n00:00:02,200 --> 00:00:04,000\n第二条合成旁白\n",
        encoding="utf-8",
    )
    tts = LocalSilentTTS()
    original_tts = vp.create_tts_provider
    original_tl = vp.build_final_timeline
    vp.create_tts_provider = lambda name, **kwargs: tts
    vp.build_final_timeline = _inject_blocked_timeline(original_tl)
    raised = None
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            vp.main(
                srt_path=srt_path,
                output_dir=out,
                renderer_name="whiteboard",
                tts_name="local-silent",
                image_path=ROCKET_IMAGE,
            )
    except vp.TimelineContractError as exc:
        raised = exc
    finally:
        vp.create_tts_provider = original_tts
        vp.build_final_timeline = original_tl

    _assert(raised is not None, "全链路遇到阻塞冲突必须失败退出")
    manifest_path = out / "run-manifest.json"
    _assert(manifest_path.exists(), "失败也应有诊断 manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _assert(manifest.get("status") == "failed", f"manifest 不得标记成功，实际={manifest.get('status')}")
    _assert(manifest.get("error", {}).get("type") == "TimelineContractError", "manifest 应记录具体错误类型")
    _assert(
        manifest.get("timeline_diagnostics", {}).get("timeline_blocked") is True,
        "manifest 应记录 timeline_blocked",
    )
    _assert(manifest.get("outputs", {}).get("final_mp4") is None, "不得声明 final_mp4 产出")
    produced = [
        item.name for item in out.iterdir()
        if item.suffix in {".mp4", ".srt"}
    ]
    _assert(not produced, f"阻塞冲突时不得留下画面/字幕成品，实际={produced}")
    RESULTS.append({
        "case": "T1d",
        "status": manifest["status"],
        "error": manifest["error"]["type"],
        "artifacts": produced,
    })


# ============================================================
# T2 单图分支时间基准
# ============================================================

def _single_image_run():
    """单图分支真实运行一次：原始 SRT 末尾 4000ms，语音实际到约 5480ms。"""
    global _IMAGE_RUN
    if _IMAGE_RUN is not None:
        return _IMAGE_RUN
    if not ROCKET_IMAGE.exists():
        raise RuntimeError(f"缺少测试图片，T2 UNVERIFIED: {ROCKET_IMAGE}")

    root = _case_dir("t2")
    out = root / "out"
    out.mkdir(parents=True, exist_ok=True)
    srt_path = root / "input.srt"
    srt_text = (
        "1\n00:00:00,000 --> 00:00:02,000\n第一条合成旁白演示\n\n"
        "2\n00:00:02,200 --> 00:00:04,000\n第二条合成旁白\n"
    )
    srt_path.write_text(srt_text, encoding="utf-8")

    tts = LocalSilentTTS(duration_overrides={2: 2.0})
    original_tts = vp.create_tts_provider
    vp.create_tts_provider = lambda name, **kwargs: tts
    log = io.StringIO()
    error = None
    started = time.time()
    try:
        with contextlib.redirect_stdout(log):
            vp.main(
                srt_path=srt_path,
                output_dir=out,
                renderer_name="whiteboard",
                tts_name="local-silent",
                image_path=ROCKET_IMAGE,
            )
    except Exception as exc:  # noqa: BLE001
        error = exc
    finally:
        vp.create_tts_provider = original_tts

    _assert(error is None, f"单图分支运行失败: {error}")
    manifest = json.loads((out / "run-manifest.json").read_text(encoding="utf-8"))
    _IMAGE_RUN = {
        "dir": out,
        "srt": srt_path,
        "srt_end_ms": 4000,
        "log": log.getvalue(),
        "manifest": manifest,
        "seconds": round(time.time() - started, 1),
        "final_mp4": out / f"{out.name}-final.mp4",
        "timed_srt": out / f"{out.name}-timed.srt",
    }
    return _IMAGE_RUN


def t2_single_image_uses_final_timeline():
    run = _single_image_run()
    manifest = run["manifest"]
    timeline = {
        "final_end_ms": manifest["audio_timeline"]["audio_end_ms"],
    }
    render_ms = (manifest.get("render_durations_ms") or [None])[0]
    _assert(render_ms is not None, "manifest 应记录 render_durations_ms")
    _assert(
        render_ms >= timeline["final_end_ms"] - 50,
        f"单图分支画面时长 {render_ms}ms 应覆盖最终语音结束 {timeline['final_end_ms']}ms",
    )
    _assert(
        render_ms > run["srt_end_ms"] + 500,
        "画面时长不应停留在原始 SRT 末尾（说明仍按旧基准渲染）",
    )
    blocks = _parse_srt_blocks(run["timed_srt"])
    _assert(len(blocks) == 2, f"timed SRT 应保留 2 条 cue，实际={len(blocks)}")
    _assert([b["text"] for b in blocks] == ["第一条合成旁白演示", "第二条合成旁白"],
            f"cue 顺序/文本被改动: {[b['text'] for b in blocks]}")
    _assert(blocks[1]["end_ms"] >= timeline["final_end_ms"] - 50,
            f"末条字幕应在语音结束之后消失，字幕结束={blocks[1]['end_ms']} 语音结束={timeline['final_end_ms']}")
    final = manifest.get("final_media") or {}
    _assert(final.get("video_stream_ms") and final.get("audio_stream_ms"),
            f"final_media 缺少流时长: {final}")
    _assert(abs(final["video_stream_ms"] - final["audio_stream_ms"]) <= 200,
            f"音画结束差超过 200ms 容差: {final}")
    RESULTS.append({
        "case": "T2",
        "srt_end_ms": run["srt_end_ms"],
        "final_end_ms": timeline["final_end_ms"],
        "render_ms": render_ms,
        "video_ms": final["video_stream_ms"],
        "audio_ms": final["audio_stream_ms"],
        "seconds": run["seconds"],
    })


def t3_no_unnecessary_tail_clone():
    """画面覆盖语音后不得再靠 tpad 克隆末帧补尾。"""
    run = _single_image_run()
    log = run["log"]
    _assert(
        "[final][extend]" not in log,
        "画面已按最终时间轴渲染时不应再触发末帧克隆; 日志片段: "
        + log[log.find("[final]"):][:300],
    )
    _assert("[final] video=" in log, "最终合并应打印音画时长对照")
    RESULTS.append({"case": "T3", "extend": False})


def t3b_tail_clone_reason_and_bound():
    """真实的尾部不足时，tpad 触发必须给出原因且补齐量在容差内。"""
    root = _case_dir("t3b")
    video = base.make_video(root / "short.mp4", 1.0, color="white")
    audio = _make_speech_file(root / "speech.wav", 2.5)
    srt_path = root / "burn.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:02,400\n尾部补齐\n", encoding="utf-8")
    final = root / "final.mp4"
    log = io.StringIO()
    with contextlib.redirect_stdout(log):
        vp.build_final_video(video, audio, srt_path, final)
    text = log.getvalue()
    _assert(final.exists(), "应成功产出 final")
    _assert("reason=render_shorter_than_audio" in text, f"末帧克隆必须说明原因，日志={text}")
    video_ms = base._probe_stream_ms(final, "v:0")
    audio_ms = base._probe_stream_ms(final, "a:0")
    _assert(abs(video_ms - audio_ms) <= 200,
            f"补齐后音画结束差应在 200ms 内: video={video_ms} audio={audio_ms}")
    RESULTS.append({"case": "T3b", "video_ms": video_ms, "audio_ms": audio_ms})


# ============================================================
# T4 诊断字段语义
# ============================================================

def t4_diagnostic_fields():
    scenarios = {
        # 自身超窗，无前序位移
        "self_overrun_only": (_entries([(1, 0, 1000, 1500)]), {1: (500, 0, 500)}),
        # 自身无超窗，但有前序位移
        "displaced_only": (
            _entries([(1, 0, 1000, 1500), (2, 1000, 2000, 900)]),
            {1: (500, 0, 500), 2: (0, 580, 480)},
        ),
        # 自身超窗且有位移
        "both": (
            _entries([(1, 0, 1000, 1500), (2, 1000, 1500, 1200)]),
            {1: (500, 0, 500), 2: (700, 580, 1280)},
        ),
        # 既无超窗也无位移
        "clean": (_entries([(1, 0, 2000, 1000)]), {1: (0, 0, 0)}),
    }
    measured = {}
    for name, (entries, expected) in scenarios.items():
        timeline = vp.build_final_timeline(entries)
        by_id = {item["id"]: item for item in timeline["entries"]}
        for cue_id, (speech_overrun, displacement, legacy) in expected.items():
            item = by_id[cue_id]
            _assert(item["speech_overrun_ms"] == speech_overrun,
                    f"{name}#{cue_id} speech_overrun_ms 期望 {speech_overrun} 实际 {item['speech_overrun_ms']}")
            _assert(item["displacement_ms"] == displacement,
                    f"{name}#{cue_id} displacement_ms 期望 {displacement} 实际 {item['displacement_ms']}")
            _assert(item["overrun_ms"] == legacy,
                    f"{name}#{cue_id} overrun_ms(旧语义) 期望 {legacy} 实际 {item['overrun_ms']}")
        _assert(
            timeline["total_speech_overrun_ms"] == sum(i["speech_overrun_ms"] for i in timeline["entries"]),
            f"{name} 汇总 speech_overrun 不一致",
        )
        _assert(
            timeline["total_displacement_ms"] == sum(i["displacement_ms"] for i in timeline["entries"]),
            f"{name} 汇总 displacement 不一致",
        )
        measured[name] = {
            item["id"]: {
                "speech_overrun_ms": item["speech_overrun_ms"],
                "displacement_ms": item["displacement_ms"],
                "overrun_ms": item["overrun_ms"],
            }
            for item in timeline["entries"]
        }
    (_case_dir("t4") / "measured.json").write_text(
        json.dumps(measured, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    RESULTS.append({"case": "T4", "scenarios": list(measured)})


# ============================================================
# T5 边界输入
# ============================================================

def t5_boundaries():
    # 零时长语音
    try:
        vp.build_final_timeline(_entries([(1, 0, 1000, 0)]))
    except ValueError:
        pass
    else:
        raise AssertionError("零时长语音必须报错")

    # 空输入
    try:
        vp.build_final_timeline([])
    except RuntimeError:
        pass
    else:
        raise AssertionError("空输入必须报错")

    # 负起点 + 倒置窗口：可自动解决的非阻塞警告
    timeline = vp.build_final_timeline(_entries([
        (1, -500, 1000, 800),
        (2, 2000, 1500, 600),
    ]))
    kinds = _kinds(timeline)
    _assert(kinds.get((1, "negative_planned_start")) == "warning", f"负起点应为 warning: {kinds}")
    _assert(kinds.get((2, "inverted_planned_window")) == "warning", f"倒置窗口应为 warning: {kinds}")
    _assert(not timeline["blocking_conflicts"], "可自动解决的冲突不得判为阻塞")
    _assert(timeline["entries"][0]["final_start_ms"] == 0, "负起点应归零")

    # 相邻 cue 边界：gap 为 0 时仍不得出现语音叠加
    tight = vp.build_final_timeline(
        _entries([(1, 0, 1000, 900), (2, 1000, 2000, 900)]),
        gap_ms=0,
    )
    starts = [item["final_start_ms"] for item in tight["entries"]]
    ends = [item["final_end_ms"] for item in tight["entries"]]
    _assert(starts[1] >= ends[0], f"相邻 cue 不得重叠: {starts} / {ends}")
    _assert(not tight["overlaps_after"], "最终时间轴不得有语音叠加")

    # 无法自动解决：字幕被挤压到语音结束之前 -> 阻塞
    blocking = vp.build_final_timeline(
        _entries([(1, 0, 5000, 3000), (2, 1000, 1500, 500)]),
        gap_ms=0,
    )
    _assert(blocking["has_blocking_conflicts"] is True, "无法满足字幕契约时须判为阻塞")

    RESULTS.append({
        "case": "T5",
        "negative_start_warning": True,
        "inverted_window_warning": True,
        "adjacent_no_overlap": True,
        "unresolvable_blocking": True,
    })


# ============================================================
# 入口
# ============================================================

CASES = [
    ("T1a", "阻塞冲突形态（真实构造）", t1a_real_blocking_shape),
    ("T1b", "prepare 门禁拒绝写盘", t1b_prepare_gate_refuses_to_write),
    ("T1c", "非阻塞警告不升级为失败", t1c_warning_is_not_upgraded),
    ("T1d", "全链路失败不产出成品", t1d_pipeline_fails_without_artifacts),
    ("T2", "单图分支使用最终时间轴", t2_single_image_uses_final_timeline),
    ("T3", "无多余尾部克隆", t3_no_unnecessary_tail_clone),
    ("T3b", "尾部克隆原因与容差", t3b_tail_clone_reason_and_bound),
    ("T4", "诊断字段四象限", t4_diagnostic_fields),
    ("T5", "边界输入", t5_boundaries),
]


def main():
    print(f"[env] work_root={WORK_ROOT}")
    print(f"[env] python={sys.executable}")
    print()
    failures = []
    for name, title, func in CASES:
        started = time.time()
        try:
            func()
        except Exception as exc:  # noqa: BLE001
            failures.append((name, f"{type(exc).__name__}: {exc}"))
            print(f"[FAIL] {name} {title}: {type(exc).__name__}: {exc}")
            continue
        print(f"[PASS] {name} {title} ({time.time() - started:.1f}s)")

    print()
    print("[MEASURED]")
    for item in RESULTS:
        print(f"  {item}")

    if failures:
        print()
        print("[SUMMARY] FAILED")
        for name, reason in failures:
            print(f"  - {name}: {reason}")
        return 1
    print()
    print("[SUMMARY] all M5-2R cases passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
