"""M4-2 音画时长与同步契约定向测试（T1–T6）。

覆盖点：
- T1 正常：多条语音均在各自 SRT 窗口内结束；校验实际放置位置、音频轨道总时长、
  最终容器中视频流 / 音频流 / 容器时长。
- T2 语音超出自身字幕窗口：语音不得被静默截断，超窗必须被明确记录。
- T3 相邻语音重叠：必须被检测并记录，语音不得被丢弃（用 PCM 能量跃升证明第二条
  语音确实放置在更早的计划起点上）。
- T4 音频长于视频：完整保留音频，视频末帧克隆延长，音画结束差 ≤ 200ms。
- T5 视频长于音频：视频不得被截断，尾部静音属预期，差值如实记录。
- T6 兼容性：既有调用方式（位置参数）与既有函数签名 / 行为不变。

约束：仅使用合成音频、合成视频、合成 SRT；不调用 TTS / Story Provider / LLM，
不跑完整流水线，不读写任何历史媒体目录。所有产物写入本次测试自建临时目录。

运行：PYTHONPATH=scripts .venv/bin/python tests/test_audio_video_duration_contract.py
"""

from __future__ import annotations

import contextlib
import inspect
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import video_pipeline as vp  # noqa: E402

WORK_ROOT = Path(
    os.environ.get("M42_CONTRACT_TMP") or tempfile.mkdtemp(prefix="m42-contract-")
)
WORK_ROOT.mkdir(parents=True, exist_ok=True)

SR = 48000
FPS = 25
FRAME_MS = int(round(1000 / FPS))

RESULTS: list[dict] = []


# ============================================================
# 媒体与探测工具（独立实现，避免用被测函数自证）
# ============================================================

def _run(cmd):
    subprocess.run([str(item) for item in cmd], check=True, capture_output=True)


def _probe_stream_ms(path, spec):
    """Independent ffprobe stream-duration probe (milliseconds)."""
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            spec,
            "-show_entries",
            "stream=duration",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    streams = json.loads(out or "{}").get("streams") or []
    if streams:
        try:
            return int(round(float(streams[0].get("duration")) * 1000))
        except (TypeError, ValueError):
            pass
    return _probe_format_ms(path)


def _probe_format_ms(path):
    out = subprocess.run(
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
    ).stdout
    value = json.loads(out or "{}").get("format", {}).get("duration")
    return int(round(float(value) * 1000)) if value else None


def make_speech(path, duration_s, freq=520):
    _run([
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"sine=frequency={freq}:duration={duration_s}:sample_rate={SR}",
        "-af", "volume=0.5",
        "-ac", "2",
        "-c:a", "pcm_s16le",
        str(path),
    ])
    return path


def make_video(path, duration_s, color="white", size="320x180"):
    _run([
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c={color}:s={size}:r={FPS}:d={duration_s}",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        str(path),
    ])
    return path


def _ts(ms):
    total = int(round(ms))
    hours, rem = divmod(total, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def write_srt(path, cues):
    lines = []
    for index, (start_ms, end_ms, text) in enumerate(cues, start=1):
        lines.append(str(index))
        lines.append(f"{_ts(start_ms)} --> {_ts(end_ms)}")
        lines.append(text)
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def extract_pcm(path, sr=SR):
    raw = path.with_suffix(".f32")
    _run([
        "ffmpeg", "-y",
        "-i", str(path),
        "-vn",
        "-ac", "1",
        "-ar", str(sr),
        "-f", "f32le",
        str(raw),
    ])
    data = np.fromfile(str(raw), dtype=np.float32)
    raw.unlink(missing_ok=True)
    return data


def detect_speech_segments(pcm, sr=SR, win_ms=20, rel=0.10, floor=2e-4,
                           min_len_ms=60, min_gap_ms=100):
    """Detect audible regions from the PCM envelope (real placement evidence)."""
    win = max(1, int(sr * win_ms / 1000))
    frames = len(pcm) // win
    if frames <= 0:
        return []
    env = np.sqrt(np.mean(np.square(pcm[: frames * win].reshape(frames, win)), axis=1))
    if env.size == 0 or float(env.max()) <= floor:
        return []
    active = env > max(floor, float(env.max()) * rel)
    raw_segments = []
    start = None
    for index, flag in enumerate(active):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            raw_segments.append([start * win_ms, index * win_ms])
            start = None
    if start is not None:
        raw_segments.append([start * win_ms, frames * win_ms])
    merged = []
    for segment in raw_segments:
        if merged and segment[0] - merged[-1][1] < min_gap_ms:
            merged[-1][1] = segment[1]
        else:
            merged.append(segment)
    return [tuple(item) for item in merged if item[1] - item[0] >= min_len_ms]


def rms_between(pcm, start_ms, end_ms, sr=SR):
    start = max(0, int(start_ms * sr / 1000))
    end = min(len(pcm), int(end_ms * sr / 1000))
    if end <= start:
        return 0.0
    return float(np.sqrt(np.mean(np.square(pcm[start:end]))))


def _case_dir(name):
    path = WORK_ROOT / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _audio_files(tmp, specs):
    """specs: [(id, planned_start_ms, planned_end_ms, speech_duration_s, freq)]"""
    files = []
    for spec in specs:
        sid, start_ms, end_ms, duration_s = spec[:4]
        freq = spec[4] if len(spec) > 4 else 520
        target = make_speech(tmp / f"tts-{sid:03d}.wav", duration_s, freq=freq)
        files.append({
            "id": sid,
            "start": _ts(start_ms),
            "end": _ts(end_ms),
            "text": f"synthetic line {sid}",
            "file": target,
        })
    return files


def _assert(condition, message):
    if not condition:
        raise AssertionError(message)


def _record(name, **values):
    RESULTS.append({"case": name, **values})


# ============================================================
# T1 正常情况
# ============================================================

def test_t1_normal_timeline():
    tmp = _case_dir("t1")
    specs = [
        (1, 0, 2000, 1.4),
        (2, 2500, 4500, 1.2),
        (3, 5000, 7000, 1.0),
    ]
    files = _audio_files(tmp, specs)
    cues = [(start, end, f"synthetic line {sid}") for sid, start, end, _ in specs]
    srt = write_srt(tmp / "t1.srt", cues)

    report = {}
    track = tmp / "t1-voice.m4a"
    vp.build_audio_track(files, track, timeline_report=report)

    durations = [entry["duration_ms"] for entry in report["entries"]]
    for measured, expected in zip(durations, [1400, 1200, 1000]):
        _assert(abs(measured - expected) <= 40, f"T1 实测时长偏差: {durations}")
    _assert(report["overlaps"] == [], "T1 不应有重叠")
    _assert(report["overruns"] == [], "T1 不应有超窗")
    _assert(abs(report["audio_end_ms"] - 6000) <= 60, f"T1 语音结束点: {report['audio_end_ms']}")
    _assert(report["srt_end_ms"] == 7000, f"T1 SRT 末尾: {report['srt_end_ms']}")

    track_ms = _probe_stream_ms(track, "a:0")
    _assert(abs(track_ms - 7000) <= 150, f"T1 音频轨道时长应补静音到 SRT 末尾: {track_ms}")

    pcm = extract_pcm(track)
    segments = detect_speech_segments(pcm)
    _assert(len(segments) == 3, f"T1 应检测到 3 段语音: {segments}")
    for segment, spec in zip(segments, specs):
        planned_start = spec[1]
        expected_len = spec[3] * 1000
        _assert(abs(segment[0] - planned_start) <= 150,
                f"T1 放置位置偏差: seg={segment} planned_start={planned_start}")
        _assert(abs((segment[1] - segment[0]) - expected_len) <= 180,
                f"T1 语音长度偏差: seg={segment} expected={expected_len}")

    base_video = make_video(tmp / "t1-base.mp4", 7.0)
    final = tmp / "t1-final.mp4"
    vp.build_final_video(base_video, track, srt, final)

    video_ms = _probe_stream_ms(final, "v:0")
    audio_ms = _probe_stream_ms(final, "a:0")
    container_ms = _probe_format_ms(final)
    _assert(abs(video_ms - 7000) <= 150, f"T1 视频流时长: {video_ms}")
    _assert(abs(video_ms - audio_ms) <= 200, f"T1 音画结束差: {video_ms} vs {audio_ms}")

    final_segments = detect_speech_segments(extract_pcm(final))
    _assert(len(final_segments) == 3, f"T1 最终容器语音段数: {final_segments}")

    _record("T1", planned_srt_end_ms=7000, speech_end_ms=report["audio_end_ms"],
            track_ms=track_ms, video_ms=video_ms, audio_ms=audio_ms,
            container_ms=container_ms, segments=final_segments)
    return tmp


# ============================================================
# T2 语音超出自身字幕窗口
# ============================================================

def test_t2_speech_overruns_window():
    tmp = _case_dir("t2")
    files = _audio_files(tmp, [(1, 0, 1000, 2.5)])
    srt = write_srt(tmp / "t2.srt", [(0, 1000, "synthetic line 1")])

    report = {}
    track = tmp / "t2-voice.m4a"
    vp.build_audio_track(files, track, timeline_report=report)

    entry = report["entries"][0]
    _assert(entry["overrun_ms"] >= 1400,
            f"T2 超窗未被记录: overrun={entry['overrun_ms']}")
    _assert(len(report["overruns"]) == 1, "T2 overruns 列表应包含该条")
    _assert(report["audio_end_ms"] >= 2400,
            f"T2 语音不得被压缩到窗口内: audio_end={report['audio_end_ms']}")

    track_ms = _probe_stream_ms(track, "a:0")
    _assert(abs(track_ms - 2500) <= 200,
            f"T2 轨道应保留完整 2.5s 语音（apad 不得裁剪）: {track_ms}")
    segments = detect_speech_segments(extract_pcm(track))
    _assert(len(segments) == 1 and abs((segments[0][1] - segments[0][0]) - 2500) <= 200,
            f"T2 语音长度应接近 2500ms: {segments}")

    base_video = make_video(tmp / "t2-base.mp4", 1.0)
    final = tmp / "t2-final.mp4"
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        vp.build_final_video(base_video, track, srt, final)
    log = buffer.getvalue()
    _assert("[final][extend]" in log, "T2 音频长于视频时应触发末帧克隆延长")

    video_ms = _probe_stream_ms(final, "v:0")
    audio_ms = _probe_stream_ms(final, "a:0")
    container_ms = _probe_format_ms(final)
    _assert(abs(video_ms - audio_ms) <= 200, f"T2 音画结束差: {video_ms} vs {audio_ms}")
    final_segments = detect_speech_segments(extract_pcm(final))
    _assert(len(final_segments) == 1
            and abs((final_segments[0][1] - final_segments[0][0]) - 2500) <= 220,
            f"T2 最终容器语音不得被截断: {final_segments}")

    _record("T2", planned_srt_end_ms=1000, speech_end_ms=report["audio_end_ms"],
            overrun_ms=entry["overrun_ms"], track_ms=track_ms, video_ms=video_ms,
            audio_ms=audio_ms, container_ms=container_ms, segments=final_segments)
    return tmp


# ============================================================
# T3 相邻语音重叠
# ============================================================

def test_t3_adjacent_overlap():
    tmp = _case_dir("t3")
    files = _audio_files(tmp, [(1, 0, 2000, 2.0, 520), (2, 1500, 3500, 1.2, 700)])
    srt = write_srt(tmp / "t3.srt", [
        (0, 2000, "synthetic line 1"),
        (1500, 3500, "synthetic line 2"),
    ])

    report = {}
    track = tmp / "t3-voice.m4a"
    vp.build_audio_track(files, track, timeline_report=report)

    _assert(len(report["overlaps"]) == 1,
            f"T3 重叠未被检测: {report['overlaps']}")
    overlap_ms = report["overlaps"][0]["overlap_ms"]
    _assert(abs(overlap_ms - 500) <= 60, f"T3 重叠量偏差: {overlap_ms}")
    _assert(report["entries"][1]["planned_start_ms"] == 1500, "T3 第二条计划起点")

    # M5-2：语音起点改为最终时间轴（避让上一条语音）。原 SRT 重叠仍如实记录，
    # 但最终播放不得出现非预期叠加。
    final_timeline = report["final_timeline"]
    second = final_timeline["entries"][1]
    _assert(report["timeline_adjusted"] is True,
            "T3 必须记录发生了时间轴调整")
    _assert(second["final_start_ms"] >= report["entries"][0]["actual_end_ms"],
            f"T3 第二条语音起点必须不早于第一条结束点: {second}")
    _assert(final_timeline["overlaps_after"] == [],
            f"T3 调整后不应再有语音重叠: {final_timeline['overlaps_after']}")

    pcm = extract_pcm(track)
    segments = detect_speech_segments(pcm)
    _assert(sum(seg[1] - seg[0] for seg in segments) >= 3100,
            f"T3 两条语音都应完整保留（总时长不得缩水）: {segments}")

    before = rms_between(pcm, 1100, 1400)
    after_shift = rms_between(pcm, 2100, 2400)
    _assert(before > 0 and after_shift > 0,
            f"T3 两段语音都应可听到: {before} -> {after_shift}")
    _assert(abs(after_shift / before - 1.0) < 0.6,
            f"T3 第二条语音不得与第一条叠加播放: {before} -> {after_shift}")

    base_video = make_video(tmp / "t3-base.mp4", 3.5)
    final = tmp / "t3-final.mp4"
    vp.build_final_video(base_video, track, srt, final)
    video_ms = _probe_stream_ms(final, "v:0")
    audio_ms = _probe_stream_ms(final, "a:0")
    container_ms = _probe_format_ms(final)
    _assert(abs(video_ms - audio_ms) <= 200, f"T3 音画结束差: {video_ms} vs {audio_ms}")

    _record("T3", planned_srt_end_ms=3500, speech_end_ms=report["audio_end_ms"],
            overlap_ms=overlap_ms, track_ms=_probe_stream_ms(track, "a:0"),
            video_ms=video_ms, audio_ms=audio_ms, container_ms=container_ms,
            segments=segments)
    return tmp


# ============================================================
# T4 音频长于视频
# ============================================================

def test_t4_audio_longer_than_video():
    tmp = _case_dir("t4")
    files = _audio_files(tmp, [(1, 0, 3000, 3.5)])
    srt = write_srt(tmp / "t4.srt", [(0, 3000, "synthetic line 1")])

    report = {}
    track = tmp / "t4-voice.m4a"
    vp.build_audio_track(files, track, timeline_report=report)
    _assert(report["audio_end_ms"] >= 3400,
            f"T4 语音结束点: {report['audio_end_ms']}")
    _assert(report["audio_beyond_srt_ms"] >= 400,
            f"T4 应记录语音超出 SRT 末尾: {report.get('audio_beyond_srt_ms')}")

    base_video = make_video(tmp / "t4-base.mp4", 3.0)
    final = tmp / "t4-final.mp4"
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        vp.build_final_video(base_video, track, srt, final)
    log = buffer.getvalue()
    _assert("[final][extend]" in log, "T4 应触发末帧克隆延长")

    video_ms = _probe_stream_ms(final, "v:0")
    audio_ms = _probe_stream_ms(final, "a:0")
    container_ms = _probe_format_ms(final)
    _assert(video_ms > 3000 + FRAME_MS, f"T4 视频应被延长: {video_ms}")
    _assert(abs(video_ms - audio_ms) <= 200, f"T4 音画结束差: {video_ms} vs {audio_ms}")
    _assert(audio_ms >= 3400, f"T4 音频不得被 -shortest 截断: {audio_ms}")

    final_segments = detect_speech_segments(extract_pcm(final))
    _assert(len(final_segments) == 1
            and abs((final_segments[0][1] - final_segments[0][0]) - 3500) <= 220,
            f"T4 语音完整性: {final_segments}")

    _record("T4", planned_srt_end_ms=3000, speech_end_ms=report["audio_end_ms"],
            track_ms=_probe_stream_ms(track, "a:0"), video_ms=video_ms,
            audio_ms=audio_ms, container_ms=container_ms, segments=final_segments)
    return tmp


# ============================================================
# T5 视频长于音频
# ============================================================

def test_t5_video_longer_than_audio():
    tmp = _case_dir("t5")
    files = _audio_files(tmp, [(1, 0, 2000, 1.0), (2, 2500, 4500, 1.0)])
    srt = write_srt(tmp / "t5.srt", [
        (0, 2000, "synthetic line 1"),
        (2500, 4500, "synthetic line 2"),
    ])

    report = {}
    track = tmp / "t5-voice.m4a"
    vp.build_audio_track(files, track, timeline_report=report)

    base_video = make_video(tmp / "t5-base.mp4", 6.0)
    final = tmp / "t5-final.mp4"
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        vp.build_final_video(base_video, track, srt, final)
    log = buffer.getvalue()
    _assert("[final][extend]" not in log, "T5 视频更长时不应延长画面")

    video_ms = _probe_stream_ms(final, "v:0")
    audio_ms = _probe_stream_ms(final, "a:0")
    container_ms = _probe_format_ms(final)
    _assert(video_ms >= 6000 - 2 * FRAME_MS, f"T5 视频不得被截断: {video_ms}")
    _assert(abs(audio_ms - report["srt_end_ms"]) <= 200,
            f"T5 音频应补静音到 SRT 末尾: {audio_ms} vs {report['srt_end_ms']}")
    delta = video_ms - audio_ms
    _assert(delta >= 0, "T5 音频不应超过视频")
    _assert(container_ms >= video_ms - FRAME_MS, f"T5 容器时长: {container_ms}")

    _record("T5", planned_srt_end_ms=report["srt_end_ms"],
            speech_end_ms=report["audio_end_ms"], track_ms=audio_ms,
            video_ms=video_ms, audio_ms=audio_ms, container_ms=container_ms,
            tail_delta_ms=delta, segments=detect_speech_segments(extract_pcm(final)))
    return tmp


# ============================================================
# T6 兼容性
# ============================================================

def test_t6_backward_compatibility():
    tmp = _case_dir("t6")

    audio_sig = inspect.signature(vp.build_audio_track)
    _assert(list(audio_sig.parameters)[:2] == ["audio_files", "output_audio"],
            f"T6 build_audio_track 前两个参数变化: {list(audio_sig.parameters)}")
    _assert(audio_sig.parameters["timeline_report"].default is None,
            "T6 timeline_report 必须为可选参数")

    final_sig = inspect.signature(vp.build_final_video)
    _assert(list(final_sig.parameters)[:5] == [
        "whiteboard_video", "audio_file", "srt_file", "output_video", "force_style"
    ], f"T6 build_final_video 参数变化: {list(final_sig.parameters)}")
    _assert(final_sig.parameters["pad_video_to_audio"].default is True,
            "T6 pad_video_to_audio 必须默认为 True 且为可选")

    files = _audio_files(tmp, [(1, 0, 2000, 1.0)])
    srt = write_srt(tmp / "t6.srt", [(0, 2000, "synthetic line 1")])

    track = tmp / "t6-voice.m4a"
    returned = vp.build_audio_track(files, track)
    _assert(Path(returned) == Path(track) and Path(track).exists(),
            "T6 旧式两参数调用返回值必须仍是输出音频路径")

    base_video = make_video(tmp / "t6-base.mp4", 2.0)
    final = tmp / "t6-final.mp4"
    vp.build_final_video(base_video, track, srt, final)
    _assert(Path(final).exists(), "T6 旧式四参数调用必须成功")

    _assert(vp.srt_time_to_seconds("00:00:01,500") == 1.5, "T6 srt_time_to_seconds 行为变化")
    _assert(vp.plan_render_duration_ms([]) == [], "T6 plan_render_duration_ms 行为变化")
    _assert(vp.ffprobe_duration_ms(track) > 0, "T6 ffprobe_duration_ms 不可用")
    _assert(callable(vp.generate_all_tts), "T6 generate_all_tts 不可调用")
    _assert(callable(vp.build_audio_timeline), "T6 时间轴分析函数缺失")

    _record("T6", audio_signature="ok", final_signature="ok",
            legacy_audio_call="ok", legacy_final_call="ok",
            video_ms=_probe_stream_ms(final, "v:0"),
            audio_ms=_probe_stream_ms(final, "a:0"))
    return tmp


# ============================================================
# runner
# ============================================================

CASES = [
    ("T1", "正常：语音均在窗口内", test_t1_normal_timeline),
    ("T2", "语音超出字幕窗口", test_t2_speech_overruns_window),
    ("T3", "相邻语音重叠", test_t3_adjacent_overlap),
    ("T4", "音频长于视频", test_t4_audio_longer_than_video),
    ("T5", "视频长于音频", test_t5_video_longer_than_audio),
    ("T6", "兼容性", test_t6_backward_compatibility),
]


def main():
    print(f"[M4-2] work root: {WORK_ROOT}")
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

    if failures:
        print()
        print("[SUMMARY] FAILED")
        for name, reason in failures:
            print(f"  - {name}: {reason}")
        return 1

    print()
    print("[SUMMARY] all T1-T6 passed")
    print("[MEASURED]")
    for item in RESULTS:
        print(f"  {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
