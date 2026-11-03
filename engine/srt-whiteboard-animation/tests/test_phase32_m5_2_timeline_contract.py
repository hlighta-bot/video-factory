"""M5-2 音画时间契约定向测试（T1–T8）。

覆盖点：
- T1 语音超出原始字幕时间窗：语音不被截断，最终字幕区间容纳语音，调整有记录。
- T2 相邻 cue 语音重叠：不产生非预期叠加；可安全调整时最终时间轴满足约束。
- T3 多 cue 连续播放：顺序、完整性、字幕与语音协调，不因局部修复破坏其他 cue。
- T4 字幕与语音时间基准一致：最终字幕来自最终时间轴，而非调整前的 SRT。
- T5 异常输入：零时长 / 空列表 / 倒置窗口 / 负起点 / 排布压力，返回明确错误或诊断。
- T6 视频结束时间：容器容纳完整音频，末帧克隆不掩盖截断，tpad 先于 subtitles。
- T7 回归：由 runner 单独执行既有测试文件（本文件不重复）。
- T8 产物保护：timed SRT 不得覆盖已有文件，冲突保护覆盖新增产物。

约束：仅使用合成音频 / 合成视频 / 合成字幕与测试替身，不调用任何真实 TTS，
不联网，不访问历史媒体目录，不执行 Git 命令。所有产物写入本次测试自建目录。

运行：PYTHONPATH=scripts .venv/bin/python tests/test_phase32_m5_2_timeline_contract.py
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import os
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

WORK_ROOT = Path(
    os.environ.get("M52_CONTRACT_TMP") or tempfile.mkdtemp(prefix="m52-contract-")
)
WORK_ROOT.mkdir(parents=True, exist_ok=True)

RESULTS: list[dict] = []


# ============================================================
# 独立工具（不复用被测函数自证）
# ============================================================

def _case_dir(name):
    path = WORK_ROOT / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _assert(condition, message):
    if not condition:
        raise AssertionError(message)


def _record(name, **values):
    RESULTS.append({"case": name, **values})


def _parse_srt_text(text):
    """独立实现的最小 SRT 解析（只取 id/start_ms/end_ms/text）。"""
    blocks = [block for block in text.replace("\r\n", "\n").split("\n\n") if block.strip()]
    parsed = []
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 3:
            raise AssertionError(f"SRT 块结构非法: {block!r}")
        index = int(lines[0].strip())
        start_raw, end_raw = [item.strip() for item in lines[1].split("-->")]
        parsed.append({
            "id": index,
            "start_ms": _ts_to_ms(start_raw),
            "end_ms": _ts_to_ms(end_raw),
            "text": "\n".join(lines[2:]).strip(),
        })
    return parsed


def _ts_to_ms(value):
    clock, _, millis = value.replace(".", ",").partition(",")
    hours, minutes, seconds = [int(item) for item in clock.split(":")]
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + int(millis or 0)


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _audio_files(tmp, specs):
    """specs: [(id, planned_start_ms, planned_end_ms, speech_seconds, freq)]"""
    files = []
    for sid, start_ms, end_ms, seconds, *rest in specs:
        freq = rest[0] if rest else 520
        target = base.make_speech(tmp / f"tts-{sid:03d}.wav", seconds, freq=freq)
        files.append({
            "id": sid,
            "start": base._ts(start_ms),
            "end": base._ts(end_ms),
            "text": f"synthetic line {sid}",
            "file": target,
        })
    return files


def _plan(tmp, specs):
    """构造 audio_files + 原始 SRT + 字幕条目 + 最终时间轴 + timed SRT。"""
    files = _audio_files(tmp, specs)
    cues = [(item[1], item[2], f"synthetic line {item[0]}") for item in specs]
    srt = base.write_srt(tmp / "input.srt", cues)
    subtitles = vp.parse_srt(str(srt))
    out_dir = tmp / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    state = {}
    final_timeline, timed_srt = vp.prepare_final_timeline(
        out_dir, files, subtitles, state
    )
    return files, srt, subtitles, final_timeline, timed_srt, state


# ============================================================
# T1 语音超出原始字幕时间窗
# ============================================================

def test_t1_speech_overruns_window():
    tmp = _case_dir("t1")
    files, srt, subtitles, tl, timed_srt, state = _plan(
        tmp, [(1, 0, 1000, 2.5)]
    )

    _assert(timed_srt is not None, "T1 应生成 timed SRT")
    entry = tl["entries"][0]
    _assert(entry["overrun_ms"] == 1500,
            f"T1 超窗量应被记录为 1500ms: {entry['overrun_ms']}")
    _assert(entry["subtitle_end_ms"] >= entry["final_end_ms"],
            f"T1 字幕结束必须容纳语音: {entry}")
    _assert(tl["status"] == "adjusted", f"T1 应标记为已调整: {tl['status']}")

    parsed = _parse_srt_text(Path(timed_srt).read_text(encoding="utf-8"))
    _assert(parsed[0]["start_ms"] == 0 and abs(parsed[0]["end_ms"] - 2500) <= 1,
            f"T1 最终字幕区间应为 0->2500ms: {parsed[0]}")
    original = _parse_srt_text(Path(srt).read_text(encoding="utf-8"))
    _assert(original[0]["end_ms"] == 1000, "T1 原始 SRT 不得被就地改写")

    report = {}
    track = tmp / "t1-voice.m4a"
    vp.build_audio_track(files, track, timeline_report=report, final_timeline=tl)
    _assert(abs(report["audio_end_ms"] - 2500) <= 50,
            f"T1 音频必须完整容纳 2500ms 语音: {report['audio_end_ms']}")
    _assert(report["timeline_adjusted"] is True, "T1 诊断必须记录时间轴已调整")
    _assert(report["final_timeline"]["entries"][0]["overrun_ms"] == 1500,
            "T1 诊断必须保留调整前后信息")

    final = tmp / "t1-final.mp4"
    base_video = base.make_video(tmp / "t1-base.mp4", 1.0)
    vp.build_final_video(base_video, track, timed_srt, final)
    video_ms = base._probe_stream_ms(final, "v:0")
    audio_ms = base._probe_stream_ms(final, "a:0")
    container_ms = base._probe_format_ms(final)
    _assert(audio_ms >= 2450, f"T1 最终音频不得被截断: {audio_ms}")
    _assert(abs(video_ms - audio_ms) <= 200, f"T1 音画结束差: {video_ms} vs {audio_ms}")

    segments = base.detect_speech_segments(base.extract_pcm(final))
    _assert(len(segments) == 1 and abs((segments[0][1] - segments[0][0]) - 2500) <= 220,
            f"T1 最终容器语音完整性: {segments}")
    _assert(parsed[0]["end_ms"] >= segments[0][1] - 50,
            f"T1 字幕不得在语音结束前消失: srt_end={parsed[0]['end_ms']} "
            f"speech_end={segments[0][1]}")

    _record("T1",
            planned_window_ms=1000, speech_ms=entry["duration_ms"],
            overrun_ms=entry["overrun_ms"],
            srt_end_before=1000, srt_end_after=parsed[0]["end_ms"],
            audio_ms=audio_ms, video_ms=video_ms, container_ms=container_ms,
            speech_segment_ms=segments[0][1] - segments[0][0])
    return tmp


# ============================================================
# T2 相邻 cue 语音重叠
# ============================================================

def test_t2_adjacent_overlap_resolved():
    tmp = _case_dir("t2")
    files, srt, subtitles, tl, timed_srt, state = _plan(
        tmp, [(1, 0, 2000, 2.0, 520), (2, 1500, 3500, 1.2, 700)]
    )

    _assert(len(tl["overlaps_before"]) == 1,
            f"T2 调整前重叠必须被识别: {tl['overlaps_before']}")
    _assert(abs(tl["overlaps_before"][0]["overlap_ms"] - 500) <= 40,
            f"T2 调整前重叠量: {tl['overlaps_before']}")
    _assert(tl["overlaps_after"] == [],
            f"T2 调整后不得残留语音重叠: {tl['overlaps_after']}")

    first, second = tl["entries"]
    _assert(second["final_start_ms"] >= first["final_end_ms"],
            f"T2 第二条语音必须让开第一条语音: {first} / {second}")
    _assert(second["shifted_ms"] > 0, f"T2 应记录后移量: {second['shifted_ms']}")

    parsed = _parse_srt_text(Path(timed_srt).read_text(encoding="utf-8"))
    _assert(parsed[1]["start_ms"] == second["final_start_ms"],
            f"T2 字幕起点必须与语音最终起点一致: {parsed[1]} vs {second}")
    _assert(parsed[0]["end_ms"] <= parsed[1]["start_ms"],
            f"T2 相邻字幕不得重叠: {parsed}")

    report = {}
    track = tmp / "t2-voice.m4a"
    vp.build_audio_track(files, track, timeline_report=report, final_timeline=tl)
    pcm = base.extract_pcm(track)
    solo = base.rms_between(pcm, 1100, 1400)
    after_shift = base.rms_between(pcm, second["final_start_ms"] + 60,
                                   second["final_start_ms"] + 360)
    _assert(solo > 0 and after_shift > 0,
            f"T2 两段语音都应可听到: {solo} -> {after_shift}")
    _assert(abs(after_shift / solo - 1.0) < 0.6,
            f"T2 不得出现非预期叠加播放: {solo} -> {after_shift}")
    gap_rms = base.rms_between(pcm, first["final_end_ms"] + 10,
                               second["final_start_ms"] - 10)
    _assert(gap_rms < solo * 0.5,
            f"T2 两条语音之间应为静音间隔: gap={gap_rms} solo={solo}")

    final = tmp / "t2-final.mp4"
    base_video = base.make_video(tmp / "t2-base.mp4", 3.5)
    vp.build_final_video(base_video, track, timed_srt, final)
    video_ms = base._probe_stream_ms(final, "v:0")
    audio_ms = base._probe_stream_ms(final, "a:0")
    _assert(abs(video_ms - audio_ms) <= 200, f"T2 音画结束差: {video_ms} vs {audio_ms}")

    _record("T2", overlap_before_ms=tl["overlaps_before"][0]["overlap_ms"],
            overlap_after=len(tl["overlaps_after"]),
            second_shift_ms=second["shifted_ms"],
            second_final_start_ms=second["final_start_ms"],
            solo_rms=round(solo, 6), shifted_rms=round(after_shift, 6),
            gap_rms=round(gap_rms, 6), audio_ms=audio_ms, video_ms=video_ms)
    return tmp


# ============================================================
# T3 多 cue 连续播放
# ============================================================

def test_t3_multi_cue_chain():
    tmp = _case_dir("t3")
    specs = [
        (1, 0, 1500, 2.2, 520),
        (2, 1750, 3000, 2.6, 640),
        (3, 3250, 4500, 1.8, 760),
        (4, 4750, 6000, 0.9, 880),
    ]
    files, srt, subtitles, tl, timed_srt, state = _plan(tmp, specs)

    entries = tl["entries"]
    _assert([item["id"] for item in entries] == [1, 2, 3, 4],
            f"T3 cue 顺序不得改变: {[item['id'] for item in entries]}")
    for prev, cur in zip(entries, entries[1:]):
        _assert(cur["final_start_ms"] >= prev["final_end_ms"],
                f"T3 cue 顺序与静音间隔被破坏: {prev} / {cur}")
    _assert(tl["overlaps_after"] == [], f"T3 不得残留重叠: {tl['overlaps_after']}")

    parsed = _parse_srt_text(Path(timed_srt).read_text(encoding="utf-8"))
    _assert(len(parsed) == 4, f"T3 字幕条数不得减少: {len(parsed)}")
    for sub, entry in zip(parsed, entries):
        _assert(sub["id"] == entry["id"], "T3 字幕顺序与时间轴不一致")
        _assert(sub["start_ms"] == entry["subtitle_start_ms"]
                and sub["end_ms"] == entry["subtitle_end_ms"],
                f"T3 字幕区间必须来自最终时间轴: {sub} vs {entry}")
        _assert(sub["end_ms"] >= entry["final_end_ms"],
                f"T3 字幕必须在语音结束后才消失: {sub} vs {entry}")

    report = {}
    track = tmp / "t3-voice.m4a"
    vp.build_audio_track(files, track, timeline_report=report, final_timeline=tl)
    pcm = base.extract_pcm(track)
    # cue 之间是 80ms 契约间隔，检测时用 20ms 分辨率避免把间隔合并成连续段
    segments = base.detect_speech_segments(pcm, min_gap_ms=20)
    _assert(len(segments) == 4, f"T3 应检测到 4 段语音: {segments}")
    for segment, entry in zip(segments, entries):
        _assert(abs(segment[0] - entry["final_start_ms"]) <= 160,
                f"T3 语音放置与最终时间轴不符: seg={segment} entry={entry}")
        _assert(abs((segment[1] - segment[0]) - entry["duration_ms"]) <= 220,
                f"T3 语音长度偏差（不得截断/变速）: seg={segment} entry={entry}")

    final = tmp / "t3-final.mp4"
    base_video = base.make_video(tmp / "t3-base.mp4", 8.0)
    vp.build_final_video(base_video, track, timed_srt, final)
    video_ms = base._probe_stream_ms(final, "v:0")
    audio_ms = base._probe_stream_ms(final, "a:0")
    _assert(audio_ms >= tl["final_end_ms"] - 60,
            f"T3 音频必须容纳完整时间轴: {audio_ms} vs {tl['final_end_ms']}")
    # 视频更长属既有契约（尾部画面/静音），音频更长必须已被末帧克隆补齐
    _assert(video_ms >= audio_ms - 60,
            f"T3 画面不得早于音频结束: {video_ms} vs {audio_ms}")
    _assert(parsed[-1]["end_ms"] <= video_ms + 100,
            f"T3 字幕不得超出最终画面: {parsed[-1]['end_ms']} vs {video_ms}")

    _record("T3",
            cues=len(entries), status=tl["status"],
            adjusted_cues=tl["adjusted_cues"], max_shift_ms=tl["max_shift_ms"],
            final_end_ms=tl["final_end_ms"], audio_ms=audio_ms, video_ms=video_ms,
            segments=segments)
    return tmp


# ============================================================
# T4 字幕与语音时间基准一致
# ============================================================

def test_t4_subtitle_uses_final_baseline():
    tmp = _case_dir("t4")
    specs = [(1, 0, 1500, 2.2, 520), (2, 1750, 3000, 2.6, 640), (3, 3250, 4500, 1.8, 760)]
    files, srt, subtitles, tl, timed_srt, state = _plan(tmp, specs)

    original = _parse_srt_text(Path(srt).read_text(encoding="utf-8"))
    timed = _parse_srt_text(Path(timed_srt).read_text(encoding="utf-8"))

    _assert(original[1]["start_ms"] == 1750, f"T4 原始 SRT 第二条起点: {original[1]}")
    _assert(timed[1]["start_ms"] != original[1]["start_ms"],
            "T4 第二条字幕起点必须已按实际语音重排（否则仍沿用旧基准）")
    _assert(timed[1]["start_ms"] == tl["entries"][1]["final_start_ms"],
            f"T4 timed SRT 必须与最终时间轴同源: {timed[1]} vs {tl['entries'][1]}")

    report = {}
    track = tmp / "t4-voice.m4a"
    vp.build_audio_track(files, track, timeline_report=report, final_timeline=tl)
    merged = report["final_timeline"]
    _assert(merged["entries"] == tl["entries"],
            "T4 诊断中的最终时间轴必须与写入磁盘的字幕一致")

    for sub, entry in zip(timed, tl["entries"]):
        _assert(sub["start_ms"] == entry["subtitle_start_ms"]
                and sub["end_ms"] == entry["subtitle_end_ms"],
                f"T4 字幕基准错位: {sub} vs {entry}")
    _assert(state["timed_srt"] == str(timed_srt), "T4 state 必须记录 timed SRT 路径")

    _record("T4",
            original_second_start_ms=original[1]["start_ms"],
            timed_second_start_ms=timed[1]["start_ms"],
            timeline_second_start_ms=tl["entries"][1]["final_start_ms"],
            baseline="final_timeline")
    return tmp


# ============================================================
# T5 异常输入
# ============================================================

def test_t5_invalid_inputs():
    tmp = _case_dir("t5")
    cases = {}

    # 5a 零时长：必须报错，不得生成虚假成功
    try:
        vp.build_final_timeline([{
            "id": 1, "planned_start_ms": 0, "planned_end_ms": 1000,
            "planned_window_ms": 1000, "duration_ms": 0, "actual_end_ms": 0,
            "overrun_ms": 0,
        }])
        raise AssertionError("T5 零时长语音必须抛错")
    except ValueError as exc:
        cases["zero_duration"] = str(exc)

    # 5b 空输入：明确报错
    try:
        vp.build_final_timeline([])
        raise AssertionError("T5 空输入必须抛错")
    except RuntimeError as exc:
        cases["empty"] = str(exc)

    # 5c 倒置窗口：诊断记录，字幕区间按实测语音重建
    tl = vp.build_final_timeline([{
        "id": 1, "planned_start_ms": 500, "planned_end_ms": 400,
        "planned_window_ms": -100, "duration_ms": 1000,
        "actual_end_ms": 1500, "overrun_ms": 1000,
    }])
    kinds = [item["kind"] for item in tl["conflicts"]]
    _assert("inverted_planned_window" in kinds,
            f"T5 倒置窗口必须被诊断: {kinds}")
    _assert(tl["status"] == "conflict", f"T5 倒置窗口不得报成功: {tl['status']}")
    _assert(tl["entries"][0]["final_end_ms"] == 1500,
            f"T5 倒置窗口应按实测时长重建: {tl['entries'][0]}")
    cases["inverted_window"] = kinds

    # 5d 负起点：归零并记录
    tl = vp.build_final_timeline([{
        "id": 1, "planned_start_ms": -300, "planned_end_ms": 1000,
        "planned_window_ms": 1300, "duration_ms": 800,
        "actual_end_ms": 500, "overrun_ms": 0,
    }])
    kinds = [item["kind"] for item in tl["conflicts"]]
    _assert("negative_planned_start" in kinds,
            f"T5 负起点必须被诊断: {kinds}")
    _assert(tl["entries"][0]["final_start_ms"] == 0,
            f"T5 负起点必须归零: {tl['entries'][0]}")
    cases["negative_start"] = kinds

    # 5e 排布压力：单条超长语音导致后续大幅后移，必须明确记录
    entries = [
        {"id": 1, "planned_start_ms": 0, "planned_end_ms": 1000,
         "planned_window_ms": 1000, "duration_ms": 8000, "actual_end_ms": 8000,
         "overrun_ms": 7000},
        {"id": 2, "planned_start_ms": 1000, "planned_end_ms": 2000,
         "planned_window_ms": 1000, "duration_ms": 1000, "actual_end_ms": 2000,
         "overrun_ms": 0},
    ]
    tl = vp.build_final_timeline(entries)
    kinds = [item["kind"] for item in tl["conflicts"]]
    _assert("large_shift" in kinds, f"T5 大幅后移必须被诊断: {kinds}")
    _assert(tl["entries"][1]["shifted_ms"] > 5000,
            f"T5 后移量应被如实记录: {tl['entries'][1]}")
    _assert(tl["status"] == "conflict", "T5 冲突输入不得报 unchanged/adjusted 成功")
    cases["large_shift"] = {
        "kinds": kinds, "shift_ms": tl["entries"][1]["shifted_ms"],
    }

    # 5f 目录级异常输入：空字幕不得生成空文件冒充成功
    out_dir = tmp / "out-fail"
    out_dir.mkdir(parents=True, exist_ok=True)
    files = _audio_files(tmp, [(1, 0, 1000, 0.5)])
    fail_state = {}
    _, fail_path = vp.prepare_final_timeline(out_dir, files, [], fail_state)
    _assert(fail_path is None,
            f"T5 空字幕时不得产出 timed SRT: {fail_path}")
    _assert("timed_srt_fallback_reason" in fail_state,
            "T5 空字幕必须留下明确诊断而不是静默降级")
    _assert(not (out_dir / f"{out_dir.name}-timed.srt").exists(),
            "T5 失败时不得留下占位字幕文件")
    cases["empty_subtitles"] = fail_state["timed_srt_fallback_reason"]

    _record("T5", **cases)
    return tmp


# ============================================================
# T6 视频结束时间与末帧克隆
# ============================================================

def test_t6_final_container_covers_audio():
    tmp = _case_dir("t6")
    files, srt, subtitles, tl, timed_srt, state = _plan(
        tmp, [(1, 0, 1000, 2.5)]
    )

    report = {}
    track = tmp / "t6-voice.m4a"
    vp.build_audio_track(files, track, timeline_report=report, final_timeline=tl)
    track_ms = base._probe_stream_ms(track, "a:0")
    _assert(track_ms >= tl["final_end_ms"] - 60,
            f"T6 音频轨道必须覆盖最终时间轴: {track_ms} vs {tl['final_end_ms']}")

    base_video = base.make_video(tmp / "t6-base.mp4", 1.0)
    final = tmp / "t6-final.mp4"
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        vp.build_final_video(base_video, track, timed_srt, final)
    log = buffer.getvalue()
    _assert("[final][extend]" in log, "T6 音频更长时应触发末帧克隆延长")

    command = next(
        (line for line in log.splitlines() if line.startswith(">>> ffmpeg") and "subtitles=" in line),
        "",
    )
    _assert(command, f"T6 未捕获到最终合成命令: {log[-400:]}")
    tpad_at = command.find("tpad")
    subtitle_at = command.find("subtitles=")
    _assert(tpad_at != -1 and subtitle_at != -1,
            f"T6 最终命令必须同时包含 tpad 与 subtitles: {command}")
    _assert(tpad_at < subtitle_at,
            "T6 tpad 必须在 subtitles 之前，否则延长段会冻结在旧字幕上")

    video_ms = base._probe_stream_ms(final, "v:0")
    audio_ms = base._probe_stream_ms(final, "a:0")
    container_ms = base._probe_format_ms(final)
    _assert(audio_ms >= 2450, f"T6 最终音频不得被截断: {audio_ms}")
    _assert(abs(video_ms - audio_ms) <= 200, f"T6 音画结束差: {video_ms} vs {audio_ms}")
    _assert(container_ms >= audio_ms - 60,
            f"T6 容器时长必须容纳音频: {container_ms} vs {audio_ms}")

    segments = base.detect_speech_segments(base.extract_pcm(final))
    _assert(len(segments) == 1 and abs((segments[0][1] - segments[0][0]) - 2500) <= 220,
            f"T6 末帧克隆不得掩盖语音被截断: {segments}")
    parsed = _parse_srt_text(Path(timed_srt).read_text(encoding="utf-8"))
    _assert(parsed[0]["end_ms"] <= video_ms + 100,
            f"T6 字幕不应超出最终画面: {parsed[0]['end_ms']} vs {video_ms}")

    _record("T6", track_ms=track_ms, audio_ms=audio_ms, video_ms=video_ms,
            container_ms=container_ms, tpad_before_subtitles=True,
            speech_segment_ms=segments[0][1] - segments[0][0])
    return tmp


# ============================================================
# T8 重复运行与产物保护
# ============================================================

def test_t8_artifact_protection():
    tmp = _case_dir("t8")
    files, srt, subtitles, tl, timed_srt, state = _plan(
        tmp, [(1, 0, 1000, 2.0)]
    )
    _assert(timed_srt is not None and Path(timed_srt).exists(),
            "T8 首次运行应写出 timed SRT")
    digest_before = _sha256(timed_srt)
    content_before = Path(timed_srt).read_text(encoding="utf-8")

    # 8a 直接写入已存在目标必须拒绝
    try:
        vp.write_timed_srt(tl, subtitles, timed_srt)
        raise AssertionError("T8 不得覆盖已存在的 timed SRT")
    except FileExistsError as exc:
        direct_error = str(exc)

    # 8b 重复运行流水线级入口：回退原始 SRT 并留下原因，旧文件一字不动
    second_state = {}
    second_timeline, second_path = vp.prepare_final_timeline(
        Path(timed_srt).parent, files, subtitles, second_state
    )
    _assert(second_path is None,
            f"T8 冲突时必须回退而不是覆盖: {second_path}")
    _assert("timed_srt_fallback_reason" in second_state,
            "T8 回退原因必须被记录")
    _assert(_sha256(timed_srt) == digest_before
            and Path(timed_srt).read_text(encoding="utf-8") == content_before,
            "T8 旧 timed SRT 内容不得被修改")

    # 8c 覆盖保护清单必须包含新增产物
    targets = [str(item) for item in vp._target_artifact_paths(
        Path(timed_srt).parent, render_only=False
    )]
    _assert(any(item.endswith("-timed.srt") for item in targets),
            f"T8 覆盖保护清单缺少 timed SRT: {targets}")
    try:
        vp._assert_targets_do_not_exist(Path(timed_srt).parent)
        raise AssertionError("T8 覆盖保护未拦截已存在的 timed SRT")
    except RuntimeError as exc:
        guard_error = str(exc)
    _assert("-timed.srt" in guard_error,
            f"T8 覆盖保护错误信息应包含新增产物: {guard_error}")

    _record("T8", direct_error=direct_error[:120],
            fallback_reason=second_state["timed_srt_fallback_reason"][:120],
            content_unchanged=True, guard_covers_timed_srt=True,
            second_status=second_timeline["status"])
    return tmp


CASES = [
    ("T1", "语音超出原始字幕时间窗", test_t1_speech_overruns_window),
    ("T2", "相邻 cue 语音重叠", test_t2_adjacent_overlap_resolved),
    ("T3", "多 cue 连续播放", test_t3_multi_cue_chain),
    ("T4", "字幕与语音时间基准一致", test_t4_subtitle_uses_final_baseline),
    ("T5", "异常输入", test_t5_invalid_inputs),
    ("T6", "视频结束时间与末帧克隆", test_t6_final_container_covers_audio),
    ("T8", "重复运行与产物保护", test_t8_artifact_protection),
]


def main():
    print(f"[M5-2] work root: {WORK_ROOT}")
    failures = []
    for name, title, func in CASES:
        started = time.time()
        try:
            path = func()
        except AssertionError as exc:
            failures.append((name, str(exc)))
            print(f"[FAIL] {name} {title}: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001
            failures.append((name, f"{type(exc).__name__}: {exc}"))
            print(f"[ERROR] {name} {title}: {type(exc).__name__}: {exc}")
            continue
        print(f"[PASS] {name} {title} ({time.time() - started:.1f}s) -> {path}")

    print()
    if failures:
        print("[SUMMARY] FAILED")
        for name, reason in failures:
            print(f"  - {name}: {reason}")
        print()
        print("[MEASURED]")
        for item in RESULTS:
            print(f"  {item}")
        return 1

    print("[SUMMARY] all M5-2 cases passed")
    print()
    print("[MEASURED]")
    for item in RESULTS:
        print(f"  {item}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
