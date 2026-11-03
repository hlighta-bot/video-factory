"""M5-3 授权 A：U3（多 cue + 单图）与 U4（多场景 + blocking）离线测试。

覆盖点：
- U3-1 多 cue 单图：最终时间轴驱动音频/字幕/画面，字幕随长语音延伸；
- U3-2 原窗口重叠：只产生 warning（subtitle_window_capped），生产继续且字幕不重叠；
- U3-3 不可满足契约：gap=0 构造出 blocking，门禁在写盘前中断；默认 gap=80 不可达；
- U3-4 探测失败：非契约异常原样向上传播，manifest 记 failed，不留成品；
- U3-5 产物边界：blocking 时只留诊断与中间音频，不留 MP4 / timed SRT / 音轨；
- U3-6 单 cue 单图基线：短语音 + 长计划窗口，画面按计划窗口渲染；
- U4-1 多场景 + blocking：多场景编号正确，门禁在渲染前中断，渲染器从未被调用；
- U4-4 同目录重跑：M4-6 覆盖保护拒绝执行，旧产物字节不变。

约束（授权 A）：仅新增本文件；不修改任何源码；不执行 Git；不联网；不调真实 Edge TTS；
仅使用合成音频 / 合成图片 / 测试替身。全部产物写入 M53_TMP 指定目录。

运行（本机未安装 pytest，用内置 runner；装了 pytest 也可直接收集 test_* 函数）：
  PYTHONPATH=scripts .venv/bin/python tests/test_phase32_m5_3_u3u4_timeline.py
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
for _path in (TESTS_DIR, PROJECT_ROOT / "scripts"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import video_pipeline as vp  # noqa: E402
import test_audio_video_duration_contract as base  # noqa: E402
from tts.base import TTSProvider  # noqa: E402

WORK_ROOT = Path(os.environ.get("M53_TMP") or tempfile.mkdtemp(prefix="m53-"))
WORK_ROOT.mkdir(parents=True, exist_ok=True)

ROCKET_IMAGE = PROJECT_ROOT / "output" / "my-rocket" / "rocket.jpg"
FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
DEFAULT_GAP_MS = 80


class SkipCase(Exception):
    """前置条件缺失（例如测试图片不存在）时跳过，不得记为通过。"""


# ============================================================
# 通用工具
# ============================================================

def _case_dir(name):
    path = WORK_ROOT / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _require_image():
    if not ROCKET_IMAGE.exists():
        raise SkipCase(f"缺少测试图片: {ROCKET_IMAGE}")


def _srt_ms(value):
    hours, minutes, rest = value.replace(",", ".").split(":")
    seconds = float(rest)
    return int(round((int(hours) * 3600 + int(minutes) * 60 + seconds) * 1000))


def _parse_srt_blocks(path):
    blocks = []
    raw = Path(path).read_text(encoding="utf-8").strip()
    if not raw:
        return blocks
    for chunk in raw.split("\n\n"):
        lines = [line for line in chunk.splitlines() if line.strip()]
        if len(lines) < 3:
            continue
        start_s, end_s = [part.strip() for part in lines[1].split("-->")]
        blocks.append({
            "start_ms": _srt_ms(start_s),
            "end_ms": _srt_ms(end_s),
            "text": " ".join(lines[2:]).strip(),
        })
    return blocks


def _srt_time(ms):
    ms = max(0, int(round(ms)))
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


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


def _make_speech(path, seconds):
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


@contextlib.contextmanager
def _patched(attr, value):
    original = getattr(vp, attr)
    setattr(vp, attr, value)
    try:
        yield original
    finally:
        setattr(vp, attr, original)


def _inject_blocked_timeline(original):
    """替身边界声明：本函数用 gap=0 重算替换生产 gap=80 的入参，从而在
    「默认参数不可达 blocking」的前提下构造阻塞。凡使用它的用例
    （U3-3/U3-5/U4-1/U4-4）证明的是「门禁被触发后的行为」，
    不证明「默认生产参数下 blocking 可自然发生」——后者由
    tests/test_phase32_m5_3_b_timeline_contract.py::B4 单独穷举验证。"""
    """故障注入：让时间轴返回一个明确的阻塞冲突（构造生产环境不可达的形态）。"""

    def _fake(entries, **kwargs):
        timeline = original(entries, **kwargs)
        conflict = {
            "id": timeline["entries"][0]["id"],
            "kind": "subtitle_shorter_than_speech",
            "detail": "注入：字幕结束早于语音结束 1234ms",
            "severity": "blocking",
        }
        timeline["conflicts"] = list(timeline["conflicts"]) + [conflict]
        timeline["blocking_conflicts"] = [conflict]
        timeline["has_blocking_conflicts"] = True
        timeline["warnings"] = [
            item for item in timeline["conflicts"] if item.get("severity") != "blocking"
        ]
        timeline["status"] = "conflict"
        return timeline

    return _fake


def _force_gap_zero(original):
    """把 gap 强制为 0，用于复现真实实现里数学可达的 blocking 形态。"""

    def _forced(entries, **kwargs):
        kwargs["gap_ms"] = 0
        return original(entries, **kwargs)

    return _forced


def _run_main(srt_path, out_dir, image_path=None, tts=None, **kwargs):
    """跑一次 main，返回 (异常或 None, 标准输出)。"""
    provider = tts or LocalSilentTTS()
    error = None
    log = io.StringIO()
    renderer_name = kwargs.pop("renderer_name", "whiteboard")
    with _patched("create_tts_provider", lambda name, **extra: provider):
        try:
            with contextlib.redirect_stdout(log):
                vp.main(
                    srt_path=srt_path,
                    output_dir=out_dir,
                    renderer_name=renderer_name,
                    tts_name="local-silent",
                    image_path=image_path,
                    **kwargs,
                )
        except BaseException as exc:  # noqa: BLE001
            error = exc
    return error, log.getvalue()


def _manifest(out_dir):
    path = Path(out_dir) / "run-manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _artifact_files(out_dir, suffixes):
    """递归扫描成品后缀（原实现只扫顶层，子目录内的成品会漏检）。"""
    root = Path(out_dir)
    return sorted(
        str(item.relative_to(root)) for item in root.rglob("*")
        if item.is_file() and item.suffix in suffixes
    )


# ============================================================
# U3-1 多 cue + 单图：最终时间轴一致性
# ============================================================

def test_u3_1_multi_cue_single_image_timeline():
    """U3-1：6 条 cue + 单图，画面/字幕/音频共用最终时间轴，字幕跟随长语音。"""
    _require_image()
    root = _case_dir("u3_1")
    out = root / "out"
    out.mkdir(parents=True, exist_ok=True)
    srt_path = root / "input.srt"
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:01,200\n第一句台词\n\n"
        "2\n00:00:01,300 --> 00:00:02,500\n第二句台词\n\n"
        "3\n00:00:02,600 --> 00:00:03,800\n第三句台词\n\n"
        "4\n00:00:03,900 --> 00:00:05,100\n第四句台词\n\n"
        "5\n00:00:05,200 --> 00:00:06,400\n第五句台词\n\n"
        "6\n00:00:06,500 --> 00:00:07,700\n第六句台词\n",
        encoding="utf-8",
    )
    tts = LocalSilentTTS(duration_overrides={3: 3.0})
    started = time.time()
    error, _log = _run_main(srt_path, out, image_path=ROCKET_IMAGE, tts=tts)
    if error is not None:
        raise AssertionError(f"多 cue 单图运行失败: {type(error).__name__}: {error}")

    manifest = _manifest(out)
    timeline_report = manifest["timeline_diagnostics"]
    entries = manifest["audio_timeline"]["entries"]

    if len(entries) != 6:
        raise AssertionError(f"应产生 6 条时间轴条目，实际={len(entries)}")
    if entries[2]["duration_ms"] < 3700:
        raise AssertionError(
            f"第 3 条语音应为长语音（约 3800ms），实际={entries[2]['duration_ms']}ms"
        )
    if timeline_report["blocking_conflicts"]:
        raise AssertionError(
            f"该输入不应产生阻塞冲突，实际={timeline_report['blocking_conflicts']}"
        )
    if timeline_report["conflicts"]:
        raise AssertionError(f"该输入不应产生任何冲突，实际={timeline_report['conflicts']}")
    if timeline_report["total_displacement_ms"] <= 0:
        raise AssertionError("长语音之后应出现位移，total_displacement_ms 却为 0")

    # 字幕必须由最终时间轴驱动：第 3 条字幕延伸到语音真正结束
    timed_srt = Path(manifest["outputs"]["timed_srt"])
    if not timed_srt.exists():
        raise AssertionError("缺少 timed SRT 产物")
    blocks = _parse_srt_blocks(timed_srt)
    if len(blocks) != 6:
        raise AssertionError(f"timed SRT 应含 6 条字幕，实际={len(blocks)}")
    if blocks[2]["end_ms"] < entries[2]["actual_end_ms"] - 1:
        raise AssertionError(
            f"第 3 条字幕结束应覆盖语音结束 {entries[2]['actual_end_ms']}ms，"
            f"实际={blocks[2]['end_ms']}ms"
        )
    if blocks[2]["start_ms"] != 2600:
        raise AssertionError(f"第 3 条字幕起点应为 2600ms，实际={blocks[2]['start_ms']}ms")
    for index in range(1, len(blocks)):
        if blocks[index]["start_ms"] < blocks[index - 1]["end_ms"] + 1:
            raise AssertionError(
                f"timed SRT 出现重叠：#{index} {blocks[index - 1]} -> {blocks[index]}"
            )
    for index, block in enumerate(blocks, start=1):
        if block["end_ms"] <= block["start_ms"]:
            raise AssertionError(f"第 {index} 条字幕区间非法: {block}")

    # 画面时长必须等于最终时间轴终点，而不是原始 SRT 末尾
    render_durations = manifest["render_durations_ms"]
    if len(render_durations) != 1:
        raise AssertionError(f"单图分支应只有 1 段渲染时长，实际={render_durations}")
    # 最终时间轴终点 = 末条字幕结束（即 final_timeline.final_end_ms）；
    # entries[*].actual_end_ms 是探测到的语音结束点，不含位移，不能当作画面终点。
    expected_final = blocks[-1]["end_ms"]

    # 异源锚点（M5-3 B 整改）：只用「原始 SRT 窗口 + 实测语音时长 + gap 常量」
    # 独立手算最终终点，不复用 timed SRT / render_durations 的返回值。
    gap_ms = vp.DEFAULT_CUE_GAP_MS
    manual_cursor = None
    for entry in entries:
        manual_start = (
            entry["planned_start_ms"] if manual_cursor is None
            else max(entry["planned_start_ms"], manual_cursor + gap_ms)
        )
        manual_cursor = manual_start + entry["duration_ms"]
    if manual_cursor != expected_final:
        raise AssertionError(
            f"独立手算终点 {manual_cursor}ms 与 timed SRT 末尾 {expected_final}ms 不一致"
        )
    if manual_cursor != render_durations[0]:
        raise AssertionError(
            f"独立手算终点 {manual_cursor}ms 与渲染时长 {render_durations[0]}ms 不一致"
        )

    if render_durations[0] != expected_final:
        raise AssertionError(
            f"渲染时长应等于最终时间轴终点 {expected_final}ms，实际={render_durations[0]}ms"
        )
    if expected_final <= 7700:
        raise AssertionError(f"最终时间轴应超过原始 SRT 末尾 7700ms，实际={expected_final}ms")

    final_mp4 = Path(manifest["outputs"]["final_mp4"])
    if not final_mp4.exists():
        raise AssertionError("缺少 final mp4 产物")
    final_media = manifest["final_media"]
    if not final_media or final_media.get("end_delta_within_200ms") is not True:
        raise AssertionError(f"音画结束差应 <=200ms，实际={final_media}")
    # 单图分支走 render_video（rocket-whiteboard.mp4），不使用逐场景 scene_clips
    if not Path(manifest["outputs"]["render_video"]).exists():
        raise AssertionError("单图分支应产出 render_video，实际缺失")
    if manifest["outputs"]["scene_clips"]:
        raise AssertionError(
            f"单图分支不应产出逐场景切片，实际={manifest['outputs']['scene_clips']}"
        )

    print(
        f"[U3-1] cues=6 final_end={expected_final}ms render={render_durations[0]}ms "
        f"timed_srt={len(blocks)} blocks delta={final_media['end_delta_ms']}ms "
        f"elapsed={time.time() - started:.1f}s"
    )


# ============================================================
# U3-2 原窗口重叠：仅 warning，生产继续
# ============================================================

def test_u3_2_overlapping_planned_window_warning_only():
    """U3-2：计划窗口重叠只产生 subtitle_window_capped warning，字幕不重叠。"""
    root = _case_dir("u3_2")
    out = root / "out"
    out.mkdir(parents=True, exist_ok=True)
    audio_a = _make_speech(root / "a.wav", 2.0)
    audio_b = _make_speech(root / "b.wav", 1.0)
    audio_files = [
        {"id": 1, "start": _srt_time(0), "end": _srt_time(10000), "file": audio_a},
        {"id": 2, "start": _srt_time(2000), "end": _srt_time(4000), "file": audio_b},
    ]
    subtitles = [
        {"id": 1, "start_ms": 0, "end_ms": 10000, "text": "第一条"},
        {"id": 2, "start_ms": 2000, "end_ms": 4000, "text": "第二条"},
    ]
    state = {}
    with contextlib.redirect_stdout(io.StringIO()):
        timeline, timed_srt = vp.prepare_final_timeline(out, audio_files, subtitles, state)

    kinds = _kinds(timeline)
    if kinds.get((1, "subtitle_window_capped")) != "warning":
        raise AssertionError(f"挤压应为 warning，实际={kinds}")
    if timeline["blocking_conflicts"]:
        raise AssertionError(f"挤压不得升级为 blocking，实际={timeline['blocking_conflicts']}")
    if timeline["status"] != "conflict":
        raise AssertionError(f"存在 warning 时状态应为 conflict，实际={timeline['status']}")
    if timed_srt is None or not Path(timed_srt).exists():
        raise AssertionError("warning 不得阻断 timed SRT 产出")

    blocks = _parse_srt_blocks(timed_srt)
    if len(blocks) != 2:
        raise AssertionError(f"timed SRT 应含 2 条字幕，实际={len(blocks)}")
    expected_end = 2000 + DEFAULT_GAP_MS - 1
    if blocks[0]["end_ms"] != expected_end:
        raise AssertionError(
            f"第 1 条字幕应被挤压到 {expected_end}ms，实际={blocks[0]['end_ms']}ms"
        )
    if blocks[1]["start_ms"] != 2000 + DEFAULT_GAP_MS:
        raise AssertionError(f"第 2 条字幕起点应为 2080ms，实际={blocks[1]['start_ms']}ms")
    if blocks[0]["end_ms"] >= blocks[1]["start_ms"]:
        raise AssertionError("字幕出现重叠")
    entry_one = timeline["entries"][0]
    if blocks[0]["end_ms"] < entry_one["final_end_ms"]:
        raise AssertionError(
            f"字幕不得早于语音结束：字幕={blocks[0]['end_ms']}ms "
            f"语音={entry_one['final_end_ms']}ms"
        )

    print(
        f"[U3-2] capped_warning=#1 subtitle=0->{blocks[0]['end_ms']}ms "
        f"next_start={blocks[1]['start_ms']}ms status={timeline['status']}"
    )


# ============================================================
# U3-3 不可满足契约：gap=0 构造 + 门禁拦截
# ============================================================

def test_u3_3_unresolvable_contract_blocks_before_write():
    """U3-3：gap=0 时可构造真实 blocking；门禁在写盘前中断，默认 gap 下不可达。"""
    # A. 纯计算层：真实实现（非注入）即可产生 blocking
    timeline = vp.build_final_timeline(
        _entries([(1, 0, 5000, 3000), (2, 1000, 1500, 500)]),
        gap_ms=0,
    )
    kinds = _kinds(timeline)
    if kinds.get((1, "subtitle_shorter_than_speech")) != "blocking":
        raise AssertionError(f"gap=0 挤压到底应判 blocking，实际={kinds}")
    if kinds.get((1, "subtitle_window_capped")) != "warning":
        raise AssertionError(f"挤压本身仍应是 warning，实际={kinds}")
    entry_one = timeline["entries"][0]
    if not entry_one["subtitle_end_ms"] < entry_one["final_end_ms"]:
        raise AssertionError(
            f"阻塞形态要求字幕早于语音结束，实际 subtitle={entry_one['subtitle_end_ms']}ms "
            f"speech={entry_one['final_end_ms']}ms"
        )

    root = _case_dir("u3_3")
    audio_a = _make_speech(root / "a.wav", 3.0)
    audio_b = _make_speech(root / "b.wav", 0.5)
    audio_files = [
        {"id": 1, "start": _srt_time(0), "end": _srt_time(5000), "file": audio_a},
        {"id": 2, "start": _srt_time(1000), "end": _srt_time(1500), "file": audio_b},
    ]
    subtitles = [
        {"id": 1, "start_ms": 0, "end_ms": 5000, "text": "第一条"},
        {"id": 2, "start_ms": 1000, "end_ms": 1500, "text": "第二条"},
    ]

    # B. gap 强制为 0：门禁必须抛 TimelineContractError 且不写 timed SRT
    out_blocked = root / "blocked"
    out_blocked.mkdir(parents=True, exist_ok=True)
    state = {}
    raised = None
    with _patched("build_final_timeline", _force_gap_zero(vp.build_final_timeline)):
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                vp.prepare_final_timeline(out_blocked, audio_files, subtitles, state)
        except vp.TimelineContractError as exc:
            raised = exc
    if raised is None:
        raise AssertionError("gap=0 的不可满足契约必须抛 TimelineContractError")
    if "subtitle_shorter_than_speech" not in str(raised):
        raise AssertionError(f"诊断应指出具体冲突类型，实际={raised}")
    if state.get("timeline_blocked") is not True or not state.get("blocking_conflicts"):
        raise AssertionError(f"state 应记录阻塞，实际={state.get('timeline_blocked')}")
    if any(out_blocked.glob("*-timed.srt")):
        raise AssertionError("门禁必须在写盘前中断，但 timed SRT 已存在")
    if state.get("timed_srt"):
        raise AssertionError("阻塞时不得登记 timed_srt")

    # C. 生产默认 gap=80：同一组音频不阻塞（blocking 在生产参数下数学不可达）
    out_default = root / "default"
    out_default.mkdir(parents=True, exist_ok=True)
    state_default = {}
    with contextlib.redirect_stdout(io.StringIO()):
        timeline_default, timed_srt_default = vp.prepare_final_timeline(
            out_default, audio_files, subtitles, state_default
        )
    if timeline_default["blocking_conflicts"]:
        raise AssertionError(
            f"默认 gap 下不应出现 blocking，实际={timeline_default['blocking_conflicts']}"
        )
    if timed_srt_default is None:
        raise AssertionError("默认 gap 下应正常产出 timed SRT")

    print(
        f"[U3-3] gap=0 blocking=#{timeline['blocking_conflicts'][0]['id']} "
        f"subtitle_end={entry_one['subtitle_end_ms']}ms speech_end={entry_one['final_end_ms']}ms "
        f"default_gap_blocking={len(timeline_default['blocking_conflicts'])}"
    )


# ============================================================
# U3-4 探测失败：异常向上传播且不留成品
# ============================================================

def test_u3_4_probe_failure_propagates_without_artifacts():
    """U3-4：音频探测失败时非契约异常原样传播，manifest 记 failed，不留成品。"""
    _require_image()
    root = _case_dir("u3_4")
    out = root / "out"
    out.mkdir(parents=True, exist_ok=True)
    srt_path = root / "input.srt"
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:01,500\n第一句台词\n\n"
        "2\n00:00:01,600 --> 00:00:03,000\n第二句台词\n",
        encoding="utf-8",
    )

    def _boom(entries):
        raise ValueError("模拟 ffprobe 探测失败")

    with _patched("build_audio_timeline", _boom):
        error, _log = _run_main(srt_path, out, image_path=ROCKET_IMAGE)

    if error is None:
        raise AssertionError("探测失败必须向上抛出，不得被吞掉")
    if not isinstance(error, ValueError):
        raise AssertionError(f"异常类型应保持原样 ValueError，实际={type(error).__name__}")
    if isinstance(error, vp.TimelineContractError):
        raise AssertionError("非契约失败不得被包装成 TimelineContractError")

    manifest = _manifest(out)
    if manifest["status"] != "failed":
        raise AssertionError(f"manifest 状态应为 failed，实际={manifest['status']}")
    if manifest["error"]["type"] != "ValueError":
        raise AssertionError(f"manifest 应记录 ValueError，实际={manifest['error']}")
    if manifest["stage_reached"] != "audio_timeline":
        raise AssertionError(f"失败阶段应为 audio_timeline，实际={manifest['stage_reached']}")
    if manifest["outputs"]["final_mp4"]:
        raise AssertionError("失败不得声明 final_mp4")
    produced = _artifact_files(out, {".mp4", ".srt", ".m4a"})
    if produced:
        raise AssertionError(f"失败不得留下成品，实际={produced}")

    print(f"[U3-4] error=ValueError stage={manifest['stage_reached']} artifacts={produced}")


# ============================================================
# U3-5 产物边界：blocking 时只有诊断与中间音频
# ============================================================

def test_u3_5_blocked_run_artifact_boundary():
    """U3-5：blocking 时不得产出 MP4 / timed SRT / 音轨，只留诊断与中间 TTS 音频。"""
    _require_image()
    root = _case_dir("u3_5")
    out = root / "out"
    out.mkdir(parents=True, exist_ok=True)
    srt_path = root / "input.srt"
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n第一句台词\n\n"
        "2\n00:00:02,200 --> 00:00:04,000\n第二句台词\n",
        encoding="utf-8",
    )
    with _patched("build_final_timeline", _inject_blocked_timeline(vp.build_final_timeline)):
        error, _log = _run_main(srt_path, out, image_path=ROCKET_IMAGE)

    if not isinstance(error, vp.TimelineContractError):
        raise AssertionError(f"应抛 TimelineContractError，实际={error!r}")

    produced = _artifact_files(out, {".mp4", ".srt", ".m4a"})
    if produced:
        raise AssertionError(f"阻塞时不得留下成品（MP4/SRT/M4A），实际={produced}")
    if not (out / "scene.json").exists():
        raise AssertionError("规划阶段的 scene.json 应保留用于诊断")
    if not (out / "run-manifest.json").exists():
        raise AssertionError("失败诊断 manifest 应存在")
    tts_dir = out / ".tts"
    if not tts_dir.is_dir():
        raise AssertionError("中间 TTS 目录应存在（音频在门禁之前生成）")
    tts_files = sorted(item.name for item in tts_dir.iterdir() if item.is_file())
    if len(tts_files) != 2:
        raise AssertionError(f"应有 2 个中间语音文件，实际={tts_files}")

    manifest = _manifest(out)
    if manifest["status"] != "failed":
        raise AssertionError(f"manifest 状态应为 failed，实际={manifest['status']}")
    if manifest["error"]["type"] != "TimelineContractError":
        raise AssertionError(
            f"manifest 应记录 TimelineContractError，实际={manifest['error']}"
        )
    if manifest["timeline_diagnostics"]["timeline_blocked"] is not True:
        raise AssertionError("manifest 应记录 timeline_blocked")
    outputs = manifest["outputs"]
    for key in ("timed_srt", "final_mp4", "render_video"):
        if outputs[key]:
            raise AssertionError(f"阻塞时 outputs.{key} 必须为空，实际={outputs[key]}")
    # audio_track 路径在门禁之前就已登记（源码在 prepare_final_timeline 前设置
    # state["audio_track"]），但混流发生在渲染之后，因此文件必须不存在。
    if outputs["audio_track"] and Path(outputs["audio_track"]).exists():
        raise AssertionError(f"阻塞时不得产出音轨文件，实际={outputs['audio_track']}")
    if outputs["scene_clips"]:
        raise AssertionError(f"阻塞时不得有 scene clip，实际={outputs['scene_clips']}")
    artifact_suffixes = {
        Path(item["path"]).suffix for item in manifest["artifacts"] if item.get("path")
    }
    if ".mp4" in artifact_suffixes:
        raise AssertionError(f"artifacts 不得登记 MP4，实际={artifact_suffixes}")

    print(
        f"[U3-5] error=TimelineContractError intermediates={tts_files} "
        f"artifacts={sorted(artifact_suffixes)}"
    )


# ============================================================
# U3-6 单 cue + 单图基线
# ============================================================

def test_u3_6_single_cue_single_image_baseline():
    """U3-6：单 cue 短语音 + 长计划窗口，画面按计划窗口 1500ms 渲染。"""
    _require_image()
    root = _case_dir("u3_6")
    out = root / "out"
    out.mkdir(parents=True, exist_ok=True)
    srt_path = root / "input.srt"
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:01,500\n单句台词\n",
        encoding="utf-8",
    )
    error, _log = _run_main(srt_path, out, image_path=ROCKET_IMAGE)
    if error is not None:
        raise AssertionError(f"单 cue 运行失败: {type(error).__name__}: {error}")

    manifest = _manifest(out)
    timeline_report = manifest["timeline_diagnostics"]
    if timeline_report["conflicts"]:
        raise AssertionError(f"单 cue 不应有冲突，实际={timeline_report['conflicts']}")
    if timeline_report["status"] == "conflict":
        raise AssertionError("单 cue 状态不应为 conflict")

    entries = manifest["audio_timeline"]["entries"]
    if len(entries) != 1:
        raise AssertionError(f"应只有 1 条时间轴条目，实际={len(entries)}")
    if entries[0]["actual_end_ms"] >= 1500:
        raise AssertionError(f"语音应短于计划窗口，实际={entries[0]['actual_end_ms']}ms")

    render_durations = manifest["render_durations_ms"]
    if len(render_durations) != 1 or render_durations[0] != 1500:
        raise AssertionError(f"单 cue 渲染时长应为计划窗口 1500ms，实际={render_durations}")

    blocks = _parse_srt_blocks(Path(manifest["outputs"]["timed_srt"]))
    if len(blocks) != 1 or blocks[0]["start_ms"] != 0 or blocks[0]["end_ms"] != 1500:
        raise AssertionError(f"单 cue 字幕应为 0->1500ms，实际={blocks}")
    if not Path(manifest["outputs"]["final_mp4"]).exists():
        raise AssertionError("缺少 final mp4 产物")
    final_media = manifest["final_media"]
    if not final_media or final_media.get("end_delta_within_200ms") is not True:
        raise AssertionError(f"音画结束差应 <=200ms，实际={final_media}")

    print(
        f"[U3-6] speech_end={entries[0]['actual_end_ms']}ms render={render_durations[0]}ms "
        f"status={timeline_report['status']} delta={final_media['end_delta_ms']}ms"
    )


# ============================================================
# U4-1 多场景 + blocking：门禁在渲染前中断
# ============================================================

_MULTISCENE_RUN: dict | None = None


def _multiscene_blocked_run():
    """多场景（4 cue 自动分组为 2 场）+ 注入 blocking，只跑一次供 U4-1 / U4-4 共享。"""
    global _MULTISCENE_RUN
    if _MULTISCENE_RUN is not None:
        return _MULTISCENE_RUN

    root = _case_dir("u4")
    out = root / "out"
    out.mkdir(parents=True, exist_ok=True)
    srt_path = root / "input.srt"
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:02,500\n第一段画面说明\n\n"
        "2\n00:00:02,500 --> 00:00:05,000\n第二段画面说明\n\n"
        "3\n00:00:05,000 --> 00:00:07,500\n第三段画面说明\n\n"
        "4\n00:00:07,500 --> 00:00:10,000\n第四段画面说明\n",
        encoding="utf-8",
    )

    renderer_calls: list[str] = []
    original_create_renderer = vp.create_renderer

    def _guard_renderer(name, **kwargs):
        renderer_calls.append(name)
        return original_create_renderer(name, **kwargs)

    tts = LocalSilentTTS()
    error = None
    log = io.StringIO()
    started = time.time()
    with _patched("build_final_timeline", _inject_blocked_timeline(vp.build_final_timeline)):
        with _patched("create_tts_provider", lambda name, **extra: tts):
            with _patched("create_renderer", _guard_renderer):
                try:
                    with contextlib.redirect_stdout(log):
                        vp.main(
                            srt_path=srt_path,
                            output_dir=out,
                            renderer_name="whiteboard",
                            tts_name="local-silent",
                            image_path=None,
                        )
                except BaseException as exc:  # noqa: BLE001
                    error = exc

    _MULTISCENE_RUN = {
        "root": root,
        "out": out,
        "srt": srt_path,
        "error": error,
        "renderer_calls": renderer_calls,
        "log": log.getvalue(),
        "seconds": round(time.time() - started, 1),
    }
    return _MULTISCENE_RUN


def test_u4_1_multiscene_blocking_gate_before_render():
    """U4-1：多场景 + blocking，门禁在渲染阶段之前中断，渲染器从未被调用。"""
    run = _multiscene_blocked_run()
    error = run["error"]
    if not isinstance(error, vp.TimelineContractError):
        raise AssertionError(f"多场景阻塞应抛 TimelineContractError，实际={error!r}")
    if run["renderer_calls"]:
        raise AssertionError(f"门禁应在渲染前中断，但渲染器被调用={run['renderer_calls']}")

    out = run["out"]
    scene_payload = json.loads((out / "scene.json").read_text(encoding="utf-8"))
    scenes = scene_payload.get("scenes") if isinstance(scene_payload, dict) else scene_payload
    if not isinstance(scenes, list) or len(scenes) != 2:
        raise AssertionError(f"4 条 cue 应分组为 2 个场景，实际={scenes}")

    manifest = _manifest(out)
    if manifest["status"] != "failed":
        raise AssertionError(f"manifest 状态应为 failed，实际={manifest['status']}")
    if manifest["error"]["type"] != "TimelineContractError":
        raise AssertionError(f"manifest 应记录 TimelineContractError，实际={manifest['error']}")
    if manifest["stage_reached"] != "audio_timeline":
        raise AssertionError(f"阻断阶段应为 audio_timeline，实际={manifest['stage_reached']}")
    if manifest["timeline_diagnostics"]["timeline_blocked"] is not True:
        raise AssertionError("manifest 应记录 timeline_blocked")
    if manifest["render_durations_ms"]:
        raise AssertionError(
            f"阻断时不得计算渲染时长，实际={manifest['render_durations_ms']}"
        )
    if manifest["outputs"]["scene_clips"]:
        raise AssertionError(
            f"阻断时不得产出 scene clip，实际={manifest['outputs']['scene_clips']}"
        )
    produced = _artifact_files(out, {".mp4", ".m4a", ".srt"})
    if produced:
        raise AssertionError(f"阻断时不得留下画面/字幕/音轨成品，实际={produced}")
    tts_dir = out / ".tts"
    tts_files = (
        sorted(item.name for item in tts_dir.iterdir() if item.is_file())
        if tts_dir.is_dir() else []
    )
    if len(tts_files) != 4:
        raise AssertionError(f"多场景应有 4 个中间语音文件，实际={tts_files}")

    print(
        f"[U4-1] scenes={len(scenes)} renderer_calls={len(run['renderer_calls'])} "
        f"stage={manifest['stage_reached']} tts={len(tts_files)} elapsed={run['seconds']}s"
    )


# ============================================================
# U4-4 同目录重跑：覆盖保护，旧产物不变
# ============================================================

def test_u4_4_rerun_same_output_dir_refused():
    """U4-4：同目录重跑被 M4-6 覆盖保护拒绝，旧产物逐字节不变。"""
    run = _multiscene_blocked_run()
    out = run["out"]
    manifest_path = out / "run-manifest.json"
    before_manifest = manifest_path.read_bytes()
    before_files = _artifact_files(out, {".mp4", ".srt", ".m4a", ".json", ".wav"})
    before_digests = {
        rel: hashlib.sha256((out / rel).read_bytes()).hexdigest()
        for rel in before_files
    }

    second_error = None
    with _patched("build_final_timeline", _inject_blocked_timeline(vp.build_final_timeline)):
        with _patched("create_tts_provider", lambda name, **extra: LocalSilentTTS()):
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    vp.main(
                        srt_path=run["srt"],
                        output_dir=out,
                        renderer_name="whiteboard",
                        tts_name="local-silent",
                        image_path=None,
                    )
            except BaseException as exc:  # noqa: BLE001
                second_error = exc

    if not isinstance(second_error, RuntimeError):
        raise AssertionError(f"重跑应被 RuntimeError 拒绝，实际={second_error!r}")
    if isinstance(second_error, vp.TimelineContractError):
        raise AssertionError("重跑应在时间轴门禁之前被覆盖保护拦下")
    if "拒绝覆盖" not in str(second_error):
        raise AssertionError(f"拒绝原因应明确为覆盖保护，实际={second_error}")

    if manifest_path.read_bytes() != before_manifest:
        raise AssertionError("覆盖保护下 run-manifest.json 被改写")
    after_files = _artifact_files(out, {".mp4", ".srt", ".m4a", ".json", ".wav"})
    after_digests = {
        rel: hashlib.sha256((out / rel).read_bytes()).hexdigest()
        for rel in after_files
    }
    if after_files != before_files:
        raise AssertionError(
            f"覆盖保护下文件集合发生变化：{set(after_files) ^ set(before_files)}"
        )
    if after_digests != before_digests:
        changed = [
            rel for rel in before_digests
            if before_digests[rel] != after_digests.get(rel)
        ]
        raise AssertionError(f"覆盖保护下文件内容被改写：{changed}")
    if _artifact_files(out, {".mp4", ".srt", ".m4a"}):
        raise AssertionError("重跑被拒后出现了新的成品")

    print(
        f"[U4-4] refused={type(second_error).__name__} files_unchanged={len(after_files)} "
        f"content_hashes_unchanged=True manifest_unchanged=True"
    )


# ============================================================
# runner
# ============================================================

CASES = [
    ("U3-1", test_u3_1_multi_cue_single_image_timeline),
    ("U3-2", test_u3_2_overlapping_planned_window_warning_only),
    ("U3-3", test_u3_3_unresolvable_contract_blocks_before_write),
    ("U3-4", test_u3_4_probe_failure_propagates_without_artifacts),
    ("U3-5", test_u3_5_blocked_run_artifact_boundary),
    ("U3-6", test_u3_6_single_cue_single_image_baseline),
    ("U4-1", test_u4_1_multiscene_blocking_gate_before_render),
    ("U4-4", test_u4_4_rerun_same_output_dir_refused),
]


def main():
    print(f"[work] M53_TMP={WORK_ROOT}")
    results = []
    for case_id, func in CASES:
        started = time.time()
        try:
            func()
            results.append((case_id, "PASS", ""))
        except SkipCase as exc:
            results.append((case_id, "SKIP", str(exc)))
        except Exception as exc:  # noqa: BLE001
            results.append((case_id, "FAIL", f"{type(exc).__name__}: {exc}"))
            traceback.print_exc()
        print(f"[{results[-1][1]}] {case_id} ({time.time() - started:.1f}s)")

    print("\n=== summary ===")
    for case_id, status, detail in results:
        print(f"{case_id}\t{status}\t{detail}")
    failed = [item for item in results if item[1] == "FAIL"]
    print(
        f"\nPASS={sum(1 for item in results if item[1] == 'PASS')} "
        f"FAIL={len(failed)} SKIP={sum(1 for item in results if item[1] == 'SKIP')}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
