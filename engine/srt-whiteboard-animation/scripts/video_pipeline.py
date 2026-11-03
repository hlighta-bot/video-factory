import argparse
from collections import Counter
import json
import math
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from merge_scenes import main as merge_scenes_main
from image_generation.sensenova import (
    SenseNovaImageProvider,
    build_scene_image_prompt,
)
from parse_srt import parse_srt as parse_srt_cues
from scene.asset_bridge import auto_annotate_bridge
from scene.asset_resolver import resolve_assets
from scene.model import Scene
from scene.planner import plan_srt_grouped
from scene.visual_representation import resolve_visual_representation
from scene.whiteboard_bridge import bridge_whiteboard
from tts.router import create_tts_provider
from renderers.router import create_renderer
from renderers.selector import select_renderer


ROOT = Path(__file__).resolve().parent.parent

# ============================================================
# TTS 配置
# ============================================================

TTS_VOICE = "zh-CN-XiaoxiaoNeural"
TTS_RATE = "-10%"
TTS_PITCH = "-1Hz"


# ============================================================
# 基础工具
# ============================================================

def run(cmd):
    print()
    print(">>>", " ".join(map(str, cmd)))
    subprocess.run(cmd, check=True)


def parse_srt(srt_path):

    text = Path(srt_path).read_text(
        encoding="utf-8"
    )

    blocks = []

    for block in text.strip().split("\n\n"):

        lines = [
            x.strip()
            for x in block.splitlines()
            if x.strip()
        ]

        if len(lines) < 3:
            continue

        number = lines[0]
        timing = lines[1]

        subtitle = " ".join(lines[2:])

        start, end = [
            x.strip()
            for x in timing.split("-->")
        ]

        blocks.append({
            "id": int(number),
            "start": start,
            "end": end,
            "text": subtitle
        })

    return blocks


def srt_time_to_seconds(value):

    value = value.replace(",", ".")

    h, m, s = value.split(":")

    return (
        int(h) * 3600
        + int(m) * 60
        + float(s)
    )


def _parse_srt_timestamp_ms(value) -> int:
    if isinstance(value, (int, float)):
        return int(value)

    raw_value = str(value or 0).strip()
    if not raw_value:
        return 0
    if raw_value.isdigit():
        return int(raw_value)

    parts = raw_value.replace(",", ".").split(":")
    if len(parts) != 3:
        return 0

    h, m, s = parts
    try:
        return int(srt_time_to_seconds(f"{h}:{m}:{s}") * 1000)
    except ValueError:
        return 0


def _subtitle_start_ms(subtitle):
    return _parse_srt_timestamp_ms(subtitle.get("start_ms", subtitle.get("start", 0)))


def _subtitle_end_ms(subtitle):
    return _parse_srt_timestamp_ms(subtitle.get("end_ms", subtitle.get("end", 0)))


def ffprobe_duration_ms(path):
    try:
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
    except subprocess.CalledProcessError:
        return None
    value = json.loads(result.stdout or "{}").get("format", {}).get("duration")
    return float(value) if value else 0.0


def ffprobe_stream_duration_ms(path, stream_spec):
    """Return the duration of one stream in milliseconds (None if unknown)."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                stream_spec,
                "-show_entries",
                "stream=duration",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError:
        return None
    streams = json.loads(result.stdout or "{}").get("streams") or []
    if not streams:
        return None
    try:
        return int(round(float(streams[0].get("duration")) * 1000))
    except (TypeError, ValueError):
        fallback = ffprobe_duration_ms(path)
        return int(round(fallback * 1000)) if fallback else None


def build_audio_timeline(audio_files):
    """Measure TTS speech against the original SRT windows.

    Planned times come from the SRT timestamps; actual ends come from the
    probed speech durations. Nothing here modifies audio or subtitles.
    """
    entries = []
    for item in audio_files:
        planned_start_ms = int(round(srt_time_to_seconds(item["start"]) * 1000))
        planned_end_ms = int(round(srt_time_to_seconds(item["end"]) * 1000))
        probed = ffprobe_duration_ms(item["file"])
        if probed is None:
            raise RuntimeError(f"无法读取 TTS 音频时长: {item['file']}")
        duration_ms = int(round(probed * 1000))
        actual_end_ms = planned_start_ms + duration_ms
        entries.append({
            "id": item["id"],
            "planned_start_ms": planned_start_ms,
            "planned_end_ms": planned_end_ms,
            "planned_window_ms": planned_end_ms - planned_start_ms,
            "duration_ms": duration_ms,
            "actual_end_ms": actual_end_ms,
            "overrun_ms": max(0, actual_end_ms - planned_end_ms),
        })
    return entries


def detect_audio_overlaps(entries):
    """Report speech that runs past the next utterance's planned start."""
    overlaps = []
    for prev, cur in zip(entries, entries[1:]):
        if prev["actual_end_ms"] > cur["planned_start_ms"]:
            overlaps.append({
                "prev_id": prev["id"],
                "next_id": cur["id"],
                "overlap_ms": prev["actual_end_ms"] - cur["planned_start_ms"],
            })
    return overlaps



# ============================================================
# M5-2：最终播放时间轴（语音完整优先，字幕跟随语音）
# ============================================================

# 相邻两条语音之间保留的最小间隔。原 SRT 留白足够时不会触发任何移动。
DEFAULT_CUE_GAP_MS = 80
# 单条 cue 相对原计划的累计后移超过该阈值时，视为需要人工关注的排布压力。
DEFAULT_LARGE_SHIFT_MS = 5000

# M5-2R：冲突分级。
# - warning：已在求解阶段自动修正或仅属排布压力，允许继续生产（仅打印 WARN 并落盘）。
# - blocking：核心音画时间契约（字幕不得早于语音消失）无法满足，必须阻止生产。
# 未登记的 kind 一律按 blocking 处理，避免新增冲突类型被静默降级为警告。
CONFLICT_SEVERITY = {
    "negative_planned_start": "warning",
    "inverted_planned_window": "warning",
    "subtitle_window_capped": "warning",
    "large_shift": "warning",
    "subtitle_shorter_than_speech": "blocking",
}


def conflict_severity(kind):
    return CONFLICT_SEVERITY.get(str(kind), "blocking")


class TimelineContractError(RuntimeError):
    """M5-2R：最终时间轴无法满足核心音画契约时中断生产。"""

    def __init__(self, message, conflicts=None):
        super().__init__(message)
        self.conflicts = list(conflicts or [])


def _ms_to_srt_timestamp(ms):
    total = max(0, int(round(ms)))
    hours, rem = divmod(total, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def build_final_timeline(entries, *, gap_ms=DEFAULT_CUE_GAP_MS,
                         large_shift_ms=DEFAULT_LARGE_SHIFT_MS):
    """把「原 SRT 时间窗 + 实测语音时长」合成为唯一最终播放时间轴。

    契约：
    - 语音完整优先：每条语音按实测时长完整播放，不裁剪、不变速。
    - 顺序不变：第 i 条语音的起点不早于第 i-1 条语音结束 + gap。
      原计划留白足够时保持原起点不动，不足时整体后移到最近可用位置。
    - 字幕跟随语音：字幕区间覆盖对应语音的真实播放区间，且不与相邻字幕重叠。
    - 无法自动消解的情况（非法窗口、字幕被挤压、后移过大）写入 conflicts，
      由调用方打印与落盘，不静默掩盖。
    """
    if not entries:
        raise RuntimeError("没有可用的语音条目，无法构建最终时间轴")
    if gap_ms < 0:
        raise ValueError(f"gap_ms 不能为负数：{gap_ms}")

    conflicts = []
    normalized = []
    for entry in entries:
        cue_id = entry.get("id")
        duration_ms = int(entry.get("duration_ms") or 0)
        planned_start_ms = int(entry.get("planned_start_ms") or 0)
        planned_end_ms = int(entry.get("planned_end_ms") or 0)

        if duration_ms <= 0:
            raise ValueError(
                f"cue #{cue_id} 实测语音时长非法（{duration_ms}ms），"
                "无法排布最终时间轴"
            )
        if planned_start_ms < 0:
            conflicts.append({
                "id": cue_id,
                "kind": "negative_planned_start",
                "detail": f"原计划起点 {planned_start_ms}ms 为负，已归零",
            })
            planned_start_ms = 0
        if planned_end_ms <= planned_start_ms:
            conflicts.append({
                "id": cue_id,
                "kind": "inverted_planned_window",
                "detail": (
                    f"原字幕窗口 {planned_start_ms}->{planned_end_ms}ms 非法，"
                    f"已按实测语音时长 {duration_ms}ms 重建"
                ),
            })
            planned_end_ms = planned_start_ms + duration_ms

        normalized.append((cue_id, planned_start_ms, planned_end_ms, duration_ms))

    resolved = []
    prev_end_ms = None
    for cue_id, planned_start_ms, planned_end_ms, duration_ms in normalized:
        final_start_ms = planned_start_ms
        if prev_end_ms is not None:
            final_start_ms = max(final_start_ms, prev_end_ms + gap_ms)
        final_end_ms = final_start_ms + duration_ms
        shifted_ms = final_start_ms - planned_start_ms
        resolved.append({
            "id": cue_id,
            "planned_start_ms": planned_start_ms,
            "planned_end_ms": planned_end_ms,
            "planned_window_ms": planned_end_ms - planned_start_ms,
            "duration_ms": duration_ms,
            "final_start_ms": final_start_ms,
            "final_end_ms": final_end_ms,
            "shifted_ms": shifted_ms,
            # M5-2R：overrun_ms 保留旧语义（含前序位移的合计超出量）；
            # speech_overrun_ms 只算本条自身语音相对原窗口的超出量；
            # displacement_ms 只算前序 cue 造成的起点后移量。
            "overrun_ms": max(0, final_end_ms - planned_end_ms),
            "speech_overrun_ms": max(
                0, duration_ms - (planned_end_ms - planned_start_ms)
            ),
            "displacement_ms": max(0, shifted_ms),
            "subtitle_start_ms": final_start_ms,
            "subtitle_end_ms": max(planned_end_ms, final_end_ms),
            "adjusted": bool(shifted_ms != 0 or final_end_ms > planned_end_ms),
        })
        prev_end_ms = final_end_ms

    # 字幕不得串行覆盖：后一条字幕起点即为前一条字幕的硬上界。
    for index, item in enumerate(resolved):
        if index + 1 < len(resolved):
            next_start_ms = resolved[index + 1]["subtitle_start_ms"]
            if item["subtitle_end_ms"] > next_start_ms:
                capped_ms = max(item["subtitle_start_ms"], next_start_ms - 1)
                conflicts.append({
                    "id": item["id"],
                    "kind": "subtitle_window_capped",
                    "detail": (
                        f"字幕窗口被下一条字幕起点挤压："
                        f"{item['subtitle_end_ms']}ms -> {capped_ms}ms"
                    ),
                })
                item["subtitle_end_ms"] = capped_ms
        if item["subtitle_end_ms"] < item["final_end_ms"]:
            conflicts.append({
                "id": item["id"],
                "kind": "subtitle_shorter_than_speech",
                "detail": (
                    f"字幕结束 {item['subtitle_end_ms']}ms 早于语音结束 "
                    f"{item['final_end_ms']}ms，相差 "
                    f"{item['final_end_ms'] - item['subtitle_end_ms']}ms"
                ),
            })

    for item in resolved:
        if item["shifted_ms"] > large_shift_ms:
            conflicts.append({
                "id": item["id"],
                "kind": "large_shift",
                "detail": (
                    f"为避让上一条语音后移 {item['shifted_ms']}ms "
                    f"（阈值 {large_shift_ms}ms）"
                ),
            })

    overlaps_after = detect_audio_overlaps([
        {
            "id": item["id"],
            "planned_start_ms": item["final_start_ms"],
            "actual_end_ms": item["final_end_ms"],
        }
        for item in resolved
    ])

    # M5-2R：统一标注冲突严重程度，不依赖各处 append 点是否已手写 severity。
    for conflict in conflicts:
        conflict["severity"] = conflict_severity(conflict.get("kind"))
    blocking_conflicts = [
        item for item in conflicts if item.get("severity") == "blocking"
    ]
    warnings = [item for item in conflicts if item.get("severity") != "blocking"]

    srt_end_ms = max((item["planned_end_ms"] for item in resolved), default=0)
    final_end_ms = resolved[-1]["final_end_ms"]
    adjusted_cues = sum(1 for item in resolved if item["adjusted"])
    shifted = [item for item in resolved if item["shifted_ms"] > 0]

    return {
        "gap_ms": gap_ms,
        "entries": resolved,
        "srt_end_ms": srt_end_ms,
        "final_end_ms": final_end_ms,
        "extension_ms": max(0, final_end_ms - srt_end_ms),
        "adjusted_cues": adjusted_cues,
        "shifted_cues": len(shifted),
        "max_shift_ms": max((item["shifted_ms"] for item in resolved), default=0),
        "total_shift_ms": sum(item["shifted_ms"] for item in resolved),
        "overlaps_before": detect_audio_overlaps(entries),
        "overlaps_after": overlaps_after,
        "conflicts": conflicts,
        "warnings": warnings,
        "blocking_conflicts": blocking_conflicts,
        "has_blocking_conflicts": bool(blocking_conflicts),
        "total_speech_overrun_ms": sum(item["speech_overrun_ms"] for item in resolved),
        "total_displacement_ms": sum(item["displacement_ms"] for item in resolved),
        "status": (
            "conflict" if conflicts
            else ("adjusted" if adjusted_cues else "unchanged")
        ),
    }


def format_final_timeline_summary(final_timeline):
    lines = [
        "[timeline] final playback plan (speech first, subtitles follow): "
        f"status={final_timeline['status']} "
        f"adjusted={final_timeline['adjusted_cues']}/{len(final_timeline['entries'])} "
        f"extension={final_timeline['extension_ms']}ms "
        f"max_shift={final_timeline['max_shift_ms']}ms"
    ]
    for item in final_timeline["entries"]:
        lines.append(
            f"[timeline] #{item['id']}: speech={item['duration_ms']}ms "
            f"planned={item['planned_start_ms']}->{item['planned_end_ms']}ms "
            f"final={item['final_start_ms']}->{item['final_end_ms']}ms "
            f"shift={item['shifted_ms']}ms "
            f"subtitle={item['subtitle_start_ms']}->{item['subtitle_end_ms']}ms"
        )
    for conflict in final_timeline.get("conflicts") or []:
        lines.append(
            f"[timeline][{str(conflict.get('severity') or 'warning').upper()}] "
            f"conflict #{conflict.get('id')} "
            f"kind={conflict.get('kind')}: {conflict.get('detail')}"
        )
    return "\n".join(lines)


def build_timed_srt_text(final_timeline, subtitles):
    """按最终时间轴生成字幕文本；cue 顺序与原文一一对应。"""
    by_id = {int(sub["id"]): sub for sub in subtitles}
    blocks = []
    for position, item in enumerate(final_timeline["entries"], start=1):
        sub = by_id.get(int(item["id"]))
        if sub is None:
            raise ValueError(f"最终时间轴 cue #{item['id']} 在原始字幕中不存在")
        text = (sub.get("text") or "").strip()
        if not text:
            raise ValueError(f"cue #{item['id']} 字幕文本为空")
        blocks.append(
            f"{position}\n"
            f"{_ms_to_srt_timestamp(item['subtitle_start_ms'])} --> "
            f"{_ms_to_srt_timestamp(item['subtitle_end_ms'])}\n"
            f"{text}"
        )
    return "\n\n".join(blocks) + "\n"


def write_timed_srt(final_timeline, subtitles, output_path):
    """写出与最终音频时间轴一致的字幕文件（不覆盖输入 SRT）。"""
    output_path = Path(output_path)
    text = build_timed_srt_text(final_timeline, subtitles)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        raise FileExistsError(f"拒绝覆盖已存在的字幕产物: {output_path}")
    output_path.write_text(text, encoding="utf-8")
    return output_path


def prepare_final_timeline(output_dir, audio_files, subtitles, state):
    """构建最终时间轴并生成 timed SRT，返回 (final_timeline, 烧录用 SRT 或 None)。

    timed SRT 写入失败时回退原始 SRT，并把原因记录进 state，绝不静默降级。
    """
    timeline_entries = build_audio_timeline(audio_files)
    final_timeline = build_final_timeline(timeline_entries)
    state["final_timeline"] = final_timeline
    state["timeline_entries_before"] = timeline_entries

    # M5-2R：阻塞冲突必须在写出 timed SRT 与成品之前就中断，
    # 不得把不满足核心音画契约的结果伪装成成功。
    blocking = final_timeline.get("blocking_conflicts") or []
    if blocking:
        state["blocking_conflicts"] = list(blocking)
        state["timeline_blocked"] = True
        print(format_final_timeline_summary(final_timeline))
        detail = "; ".join(
            f"#{item.get('id')} {item.get('kind')}: {item.get('detail')}"
            for item in blocking
        )
        raise TimelineContractError(
            "最终时间轴无法满足核心音画契约，已中断生产: " + detail,
            conflicts=blocking,
        )

    timed_srt_path = Path(output_dir) / f"{Path(output_dir).name}-timed.srt"
    try:
        write_timed_srt(final_timeline, subtitles, timed_srt_path)
    except (OSError, ValueError) as exc:
        state["timed_srt_fallback_reason"] = str(exc)
        print(f"[WARN] timed SRT 生成失败，回退原始 SRT: {exc}")
        print(format_final_timeline_summary(final_timeline))
        return final_timeline, None

    state["timed_srt"] = str(timed_srt_path)
    print()
    print(format_final_timeline_summary(final_timeline))
    print(f"[ok] timed srt: {timed_srt_path}")
    return final_timeline, timed_srt_path


def summarize_audio_timeline(entries, srt_end_ms=None):
    report = {
        "entries": entries,
        "overlaps": detect_audio_overlaps(entries),
        "overruns": [entry for entry in entries if entry["overrun_ms"] > 0],
        "audio_end_ms": max((entry["actual_end_ms"] for entry in entries), default=0),
        "srt_end_ms": srt_end_ms,
    }
    if srt_end_ms is not None:
        report["tail_gap_ms"] = max(0, srt_end_ms - report["audio_end_ms"])
        report["audio_beyond_srt_ms"] = max(0, report["audio_end_ms"] - srt_end_ms)
    return report


def format_audio_timeline_report(report):
    lines = ["[audio] timeline planned_start/probed_tts/actual_end"]
    for entry in report["entries"]:
        lines.append(
            f"[audio] #{entry['id']}: start={entry['planned_start_ms']}ms "
            f"window={entry['planned_window_ms']}ms "
            f"tts={entry['duration_ms']}ms end={entry['actual_end_ms']}ms "
            f"overrun={entry['overrun_ms']}ms"
        )
    for overlap in report["overlaps"]:
        lines.append(
            f"[audio][WARN] overlap #{overlap['prev_id']} -> #{overlap['next_id']}: "
            f"{overlap['overlap_ms']}ms speech runs into the next planned start "
            f"(speech kept, no truncation applied)"
        )
    for entry in report["overruns"]:
        lines.append(
            f"[audio] overrun #{entry['id']}: speech exceeds its SRT window by "
            f"{entry['overrun_ms']}ms (absorbed by the final timeline, "
            "speech kept in full)"
        )
    lines.append(f"[audio] audio_end={report['audio_end_ms']}ms")
    if report.get("srt_end_ms") is not None:
        lines.append(
            f"[audio] srt_end={report['srt_end_ms']}ms "
            f"tail_gap={report.get('tail_gap_ms')}ms "
            f"beyond_srt={report.get('audio_beyond_srt_ms')}ms"
        )
    final_timeline = report.get("final_timeline")
    if final_timeline:
        lines.append(format_final_timeline_summary(final_timeline))
        lines.append(
            f"[audio] timeline_adjusted={report.get('timeline_adjusted')} "
            f"overlaps_after={len(final_timeline.get('overlaps_after') or [])} "
            f"conflicts={len(final_timeline.get('conflicts') or [])}"
        )
    return "\n".join(lines)


def plan_render_duration_ms(scenes, final_end_ms=None, audio_duration_ms=None,
                            timeline_by_id=None):
    """Return render durations for scenes on the accepted absolute timeline.

    M5-2：timeline_by_id 用最终时间轴（实测语音时长）覆盖 cue 的显示区间，
    使画面时长与最终字幕/音频使用同一时间基准；audio_duration_ms 兜底保证
    画面至少覆盖完整旁白。
    """
    overrides = timeline_by_id or {}
    timings = []
    for scene in scenes:
        subs = list(scene.subtitles or [])
        start_ms = None
        end_ms = None
        for sub in subs:
            override = overrides.get(sub.get("id"))
            if override:
                sub_start = int(override["subtitle_start_ms"])
                sub_end = int(override["subtitle_end_ms"])
            else:
                sub_start = _subtitle_start_ms(sub)
                sub_end = _subtitle_end_ms(sub)
            start_ms = sub_start if start_ms is None else min(start_ms, sub_start)
            end_ms = max(end_ms or 0, sub_end)

        if start_ms is None:
            start_ms = int(getattr(scene, "duration_ms", 0) or 0)
        if end_ms is None:
            end_ms = int(getattr(scene, "duration_ms", 0) or start_ms)
        timings.append((start_ms, end_ms))

    if not timings:
        return []
    scene_end_ms = max(
        final_end_ms or 0,
        timings[-1][1],
        audio_duration_ms or 0,
    )

    durations = []
    for index, (start_ms, end_ms) in enumerate(timings):
        if index + 1 < len(timings):
            effective_end_ms = max(timings[index + 1][0], end_ms)
        else:
            effective_end_ms = max(scene_end_ms, end_ms)
        durations.append(max(1000, effective_end_ms - start_ms))
    return durations


# ============================================================
# TTS
# ============================================================

def generate_all_tts(provider, subtitles, tts_dir):

    tts_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    audio_files = []

    for subtitle in subtitles:

        index = subtitle["id"]

        text = subtitle["text"]

        output = (
            tts_dir
            / f"{index:03d}.mp3"
        )

        print()
        print(
            f"[TTS] {index}: {text}"
        )

        was_using_offline = bool(getattr(provider, "using_offline", False))
        provider.synthesize(
            text,
            output
        )

        # If auto fallback activates partway through a video, regenerate earlier
        # cues locally so the final narration does not mix two different voices.
        if (
            not was_using_offline
            and bool(getattr(provider, "using_offline", False))
        ):
            for previous in audio_files:
                provider.synthesize(previous["text"], previous["file"])

        audio_files.append({
            "id": index,
            "start": subtitle["start"],
            "end": subtitle["end"],
            "text": text,
            "file": output
        })

    return audio_files


def _tts_diagnostics(requested_name, provider):
    backend = getattr(provider, "backend_name", None)
    if not backend:
        backend = "espeak" if requested_name == "espeak" else "edge"
    if requested_name == "auto" and bool(getattr(provider, "using_offline", False)):
        backend = "espeak"
    info = {
        "requested": requested_name,
        "requested_backend": requested_name,
        "backend": backend,
        "actual_backend": backend,
        "provider": backend,
        "voice": getattr(provider, "voice", TTS_VOICE),
        "fallback_used": bool(getattr(provider, "fallback_used", False)),
        "offline_fallback_used": requested_name == "auto" and backend == "espeak",
        "warnings": list(getattr(provider, "warnings", []) or []),
    }
    for key in (
        "service_url", "model_name", "sample_rate", "channels",
        "request_count", "chunk_count",
    ):
        value = getattr(provider, key, None)
        if value is not None:
            info[{
                "model_name": "model",
                "request_count": "requests",
                "chunk_count": "chunks",
            }.get(key, key)] = value
    if hasattr(provider, "speed"):
        info["speed"] = provider.speed
    return info


def _create_tts_provider(name):
    return create_tts_provider(
        name,
        voice=TTS_VOICE,
        rate=TTS_RATE,
        pitch=TTS_PITCH,
    )


def _tts_pitch_label(provider):
    if getattr(provider, "backend_name", None) == "audio8":
        return "unsupported (ignored)"
    return TTS_PITCH


# ============================================================
# TTS → 按 SRT 时间轴合成完整音频
# ============================================================

def build_audio_track(
    audio_files,
    output_audio,
    timeline_report=None,
    final_timeline=None
):

    if not audio_files:
        raise RuntimeError(
            "没有可用的 TTS 音频"
        )

    output_audio = Path(output_audio)
    output_audio.parent.mkdir(parents=True, exist_ok=True)

    temp_dir = output_audio.parent / f".audio-track-tmp-{uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=True)

    placed_tracks = []

    try:
        # 先按 SRT 窗口逐条实测语音时长（只读，不裁剪、不改速、不改 SRT）
        timeline_entries = build_audio_timeline(audio_files)

        # M5-2：语音起点取最终时间轴，已消解与上一条语音的非预期重叠
        if final_timeline is None:
            final_timeline = build_final_timeline(timeline_entries)
        final_entries = final_timeline["entries"]
        if len(final_entries) != len(audio_files):
            raise RuntimeError(
                "最终时间轴条目数与 TTS 音频数不一致: "
                f"{len(final_entries)} != {len(audio_files)}"
            )

        for index, item in enumerate(audio_files):
            duration_ms = timeline_entries[index]["duration_ms"]
            delay_s = round(final_entries[index]["final_start_ms"] / 1000.0, 3)

            normalized = temp_dir / f"normalized-{index:03d}.wav"
            run([
                "ffmpeg",
                "-y",
                "-i",
                str(item["file"]),
                "-af",
                "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=N/SR/TB,loudnorm=I=-16:TP=-1.5:LRA=11",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-c:a",
                "pcm_s16le",
                str(normalized)
            ])

            placed = temp_dir / f"placed-{index:03d}.wav"
            delay_ms = int(round(delay_s * 1000))
            run([
                "ffmpeg",
                "-y",
                "-i",
                str(normalized),
                "-af",
                f"adelay={delay_ms}|{delay_ms}",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-c:a",
                "pcm_s16le",
                str(placed)
            ])

            placed_tracks.append(placed)

        srt_end_ms = max(
            (entry["planned_end_ms"] for entry in timeline_entries),
            default=0,
        )
        # M5-2：音频至少覆盖最终时间轴上的全部语音，而不是只补到原 SRT 末尾
        timeline_end_ms = int(final_timeline.get("final_end_ms") or 0)
        mix_end_ms = max(srt_end_ms, timeline_end_ms)

        inputs = []
        mix_inputs = ""

        for index, placed in enumerate(placed_tracks):
            inputs.extend(["-i", str(placed)])
            mix_inputs += f"[{index}:a]"

        # apad 只补静音、绝不裁剪：
        # 语音早于 SRT 末尾结束时，补静音到 SRT 末尾，音频流不会提前结束；
        # 语音晚于 SRT 末尾结束时，输入更长，apad 原样保留，不会截断旁白。
        pad_clause = (
            f",apad=whole_dur={mix_end_ms / 1000.0:.3f}"
            if mix_end_ms > 0
            else ""
        )

        filters = [
            (
                f"{mix_inputs}"
                f"amix="
                f"inputs={len(placed_tracks)}:"
                f"duration=longest:"
                f"dropout_transition=0:"
                f"normalize=0"
                f"{pad_clause}"
                f"[out]"
            )
        ]

        run([
            "ffmpeg",
            "-y",
            *inputs,
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[out]",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(output_audio)
        ])

        report = summarize_audio_timeline(
            timeline_entries,
            srt_end_ms=srt_end_ms or None,
        )
        # M5-2：把调整后的最终时间轴并入既有诊断结构，不另建同义字段
        report["final_timeline"] = final_timeline
        report["timeline_adjusted"] = bool(final_timeline.get("adjusted_cues"))
        report["audio_end_ms"] = timeline_end_ms or report["audio_end_ms"]
        if srt_end_ms:
            report["tail_gap_ms"] = max(0, srt_end_ms - report["audio_end_ms"])
        report["audio_beyond_srt_ms"] = max(
            0, report["audio_end_ms"] - (srt_end_ms or 0)
        )
        print()
        print(format_audio_timeline_report(report))
        if timeline_report is not None:
            timeline_report.clear()
            timeline_report.update(report)

        return output_audio

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ============================================================
# 白板 + 音频 + 字幕
# ============================================================

CINEMATIC_SUBTITLE_STYLE = (
    "FontName=Noto Sans CJK SC,FontSize=20,PrimaryColour=&H00FFFFFF,"
    "OutlineColour=&H00000000,Outline=1.2,Shadow=1.2,ShadowColour=&H66000000,"
    "MarginV=40,Bold=1"
)


def _finalize_fit_record(record, target_ms, renderer_timing):
    """M4-4：给对齐记录补上根因字段，区分渲染器编排问题与后处理安全校正。"""
    record["pre_fit_ms"] = record.get("actual_ms")
    record["renderer_plan"] = renderer_timing
    adjusted = record.get("adjusted_ms")
    record["residual_ms"] = (adjusted - target_ms) if adjusted is not None else None
    mode = record.get("mode")
    factor = record.get("speed_factor") or 1.0
    # 变速超过 1.5x 视为严重压缩，报告里不得当作完全正常
    record["severe_speedup"] = bool(mode == "speed_up" and factor >= 1.5)
    if mode == "none":
        cause = "renderer_ok"
    elif mode == "speed_up":
        cause = "renderer_overrun"
    elif mode == "extend":
        cause = "renderer_shortfall"
    else:
        cause = "not_applicable"
    if renderer_timing and renderer_timing.get("budget_insufficient"):
        # 渲染器已按预算重排，但笔画数 x 每笔最少帧仍超出目标预算
        cause = f"{cause}+renderer_budget_insufficient"
    record["cause"] = cause
    return record


def fit_clip_duration(clip_path, target_ms, label="", renderer_timing=None):
    """把渲染出的场景片段对齐到 plan_render_duration_ms 给出的目标时长。

    白板渲染的输出时长 = max(total_ms, 书写编排结束 + 500ms 凝视尾巴)，
    当 annotation 的书写编排下限（每笔最短时长等）超过 total_ms-500 时，
    成片会比目标时长更长，多场景拼接后整片超长、画面晚于 SRT 字幕与旁白。
    这里按目标时长做内容无损的对齐：
    - 片段偏长：整体变速（setpts + fps）压缩到目标时长，画面内容不丢；
    - 片段偏短：克隆末帧补齐到目标时长。
    M4-4 起，白板渲染器会先在自己的时间编排里把书写事件压进目标预算
    （scripts/renderers/whiteboard.py 的按帧预算重排），本函数保留为最终安全
    保护，不因渲染器修复而取消：渲染器放不下（例如笔画数 × 每笔最少 2 帧
    已超过预算）时，这里仍做兜底校正。

    renderer_timing: 渲染器自报的时间编排诊断（WhiteboardRenderer.last_timing_plan），
    写入记录后用 cause / severe_speedup 区分「渲染器编排问题」与「后处理安全校正」。
    返回本次对齐的事实记录，供 run-manifest.json 追溯。
    """
    clip_path = Path(clip_path)
    target_ms = int(target_ms or 0)
    record = {
        "label": label,
        "clip": str(clip_path),
        "target_ms": target_ms,
        "actual_ms": None,
        "adjusted_ms": None,
        "mode": "skipped",
        "pre_fit_ms": None,
        "residual_ms": None,
        "renderer_plan": None,
        "cause": None,
        "severe_speedup": False,
    }
    if target_ms <= 0 or not clip_path.exists():
        return _finalize_fit_record(record, target_ms, renderer_timing)

    actual_ms = ffprobe_stream_duration_ms(clip_path, "v:0")
    record["actual_ms"] = actual_ms
    if not actual_ms:
        return _finalize_fit_record(record, target_ms, renderer_timing)

    delta_ms = actual_ms - target_ms
    if abs(delta_ms) <= 40:
        record["adjusted_ms"] = actual_ms
        record["mode"] = "none"
        return _finalize_fit_record(record, target_ms, renderer_timing)

    fitted_path = clip_path.with_name(f"{clip_path.stem}.fit{clip_path.suffix}")
    if delta_ms > 0:
        factor = actual_ms / target_ms
        video_filter = f"setpts=PTS/{factor:.6f},fps=25"
        record["mode"] = "speed_up"
        record["speed_factor"] = round(factor, 4)
    else:
        video_filter = (
            f"tpad=stop_mode=clone:stop_duration={(-delta_ms) / 1000.0:.3f}"
        )
        record["mode"] = "extend"

    run([
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-i",
        str(clip_path),
        "-vf",
        video_filter,
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(fitted_path),
    ])
    fitted_path.replace(clip_path)

    record["adjusted_ms"] = ffprobe_stream_duration_ms(clip_path, "v:0")
    print(
        f"[fit][{label}] target={target_ms}ms actual={actual_ms}ms "
        f"mode={record['mode']} adjusted={record['adjusted_ms']}ms"
    )
    return _finalize_fit_record(record, target_ms, renderer_timing)


def render_single_scene_on_timeline(
    renderer_name,
    scene,
    output_path,
    target_ms,
    state,
    timing_label="single",
):
    """M5-2R：单场景/单图分支统一用最终时间轴的目标时长渲染并归一。

    画面、字幕与音频必须共用一份时间数据，避免用原始 SRT 渲染后再靠 tpad
    克隆末帧补齐而产生静帧。
    """
    target_ms = int(target_ms)
    renderer_kwargs = {}
    if renderer_name == "whiteboard":
        renderer_kwargs["fit_scene_duration"] = True
    renderer = create_renderer(renderer_name, **renderer_kwargs)
    renderer.render(replace(scene, duration_ms=target_ms), output_path)
    if renderer_name == "whiteboard":
        fit_record = fit_clip_duration(
            output_path,
            target_ms,
            label=timing_label,
            renderer_timing=getattr(renderer, "last_timing_plan", None),
        )
        if fit_record:
            state.setdefault("clip_fit", []).append(fit_record)
        _collect_timing_artifact(state, renderer)
    state["renderer"] = renderer_name
    return Path(output_path)


def build_final_video(    whiteboard_video,
    audio_file,
    srt_file,
    output_video,
    force_style=None,
    pad_video_to_audio=True
):

    # FFmpeg subtitles filter
    #
    # Linux / WSL 路径通常可以直接使用。
    # 将单引号转义，避免路径中有特殊字符。
    subtitle_path = str(
        srt_file
    ).replace(
        "\\",
        "/"
    ).replace(
        "'",
        r"\'"
    )

    subtitle_filter = (
        f"subtitles='{subtitle_path}'"
    )
    if force_style:
        subtitle_filter += f":force_style='{force_style}'"

    # 音画结束契约：不使用 -shortest。
    # -shortest 会在音频更长时直接截断尚未播完的旁白，属于静默丢语音。
    # 这里改为：音频比画面更长时，克隆末帧把画面延长到音频结束点，
    # 语音完整保留，画面也不会提前消失。
    # M5-2：tpad 必须放在 subtitles 之前。若先烧字幕再克隆末帧，延长出来的画面
    # 会冻结在克隆帧那一瞬间的字幕上，末段字幕无法随最终时间轴变化。
    video_filter = subtitle_filter
    video_stream_ms = ffprobe_stream_duration_ms(whiteboard_video, "v:0")
    audio_stream_ms = ffprobe_stream_duration_ms(audio_file, "a:0")
    tail_pad_ms = 0
    if pad_video_to_audio and video_stream_ms and audio_stream_ms and audio_stream_ms > video_stream_ms:
        tail_pad_ms = audio_stream_ms - video_stream_ms
        video_filter = (
            f"tpad=stop_mode=clone:stop_duration={tail_pad_ms / 1000.0:.3f},"
            f"{subtitle_filter}"
        )
        print(
            f"[final][extend] reason=render_shorter_than_audio "
            f"video={video_stream_ms}ms audio={audio_stream_ms}ms: "
            f"clone last frame for {tail_pad_ms}ms so the speech is never cut"
        )
    elif video_stream_ms and audio_stream_ms:
        print(
            f"[final] video={video_stream_ms}ms audio={audio_stream_ms}ms: "
            f"no frame extension needed"
        )

    run([
        "ffmpeg",
        "-y",

        "-i",
        str(whiteboard_video),

        "-i",
        str(audio_file),

        "-vf",
        video_filter,

        "-map",
        "0:v:0",
        "-map",
        "1:a:0",

        "-c:v",
        "libx264",

        "-preset",
        "medium",

        "-crf",
        "18",

        "-c:a",
        "aac",

        "-b:a",
        "192k",

        "-pix_fmt",
        "yuv420p",

        str(output_video)
    ])

    final_video_ms = ffprobe_stream_duration_ms(output_video, "v:0")
    final_audio_ms = ffprobe_stream_duration_ms(output_video, "a:0")
    if final_video_ms and final_audio_ms:
        print(
            f"[final] out video={final_video_ms}ms audio={final_audio_ms}ms "
            f"end_delta={abs(final_video_ms - final_audio_ms)}ms"
        )


# ============================================================
# Main Pipeline
# ============================================================

def _run_pipeline(
    srt_path,
    output_dir=None,
    renderer_name="auto",
    tts_name="audio8",
    render_only=False,
    env_py=None,
    asset_roots=None,
    image_path=None,
    background_map=None,
    group_target_ms=8000,
    image_provider_name="none",
    resume=False,
    state=None,
):
    """执行一次流水线；把阶段与产物写入 state 供产物清单使用。"""
    if state is None:
        state = {}

    def _stage(name):
        state["stage"] = name

    state.setdefault("audio_files", [])
    state.setdefault("scene_clips", [])
    state["srt"] = str(srt_path)
    state["image"] = str(image_path) if image_path else None
    state["requested_output_dir"] = str(output_dir) if output_dir else None

    if image_provider_name not in {"none", "sensenova"}:
        raise ValueError(f"Unsupported image provider: {image_provider_name}")
    if image_provider_name != "none" and image_path is not None:
        raise ValueError("--image and --image-provider cannot be used together")

    srt_path = Path(
        srt_path
    ).resolve()

    output_dir = Path(
        output_dir or srt_path.parent
    ).resolve()

    output_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    _stage("validate_srt")
    if not srt_path.exists():
        raise FileNotFoundError(
            srt_path
        )

    image_path = Path(
        image_path
    ).resolve() if image_path else None

    if image_path is not None and not image_path.exists():
        raise FileNotFoundError(
            image_path
        )

    env_py = Path(
        env_py or (
            ROOT
            / ".venv"
            / "bin"
            / "python"
        )
    ).resolve()

    if not env_py.exists():
        raise RuntimeError(
            "项目 .venv 不存在，请先执行 "
            "python scripts/prepare_env.py"
        )

    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "没有找到 ffmpeg"
        )

    print("=" * 60)
    print("VIDEO FACTORY PIPELINE")
    print("=" * 60)

    _stage("parse_srt")
    subtitles = parse_srt(
        srt_path
    )

    if not subtitles:
        raise RuntimeError(
            f"SRT 中没有找到有效字幕: "
            f"{srt_path}"
        )
    state["subtitle_ids"] = [str(item.get("id")) for item in subtitles]

    scene_json = (
        output_dir
        / "scene.json"
    )
    state["scene_json"] = str(scene_json)

    if image_path is not None:
        scene = Scene(
            id=output_dir.name,
            renderer=renderer_name,
            visual={
                "image": str(image_path),
                "srt": str(srt_path),
            },
            subtitles=subtitles,
        )
    else:
        raw_text = srt_path.read_text(
            encoding="utf-8-sig"
        )
        cues = parse_srt_cues(
            raw_text
        )
        planned_scenes = plan_srt_grouped(
            cues,
            renderer=renderer_name,
            target_duration_ms=group_target_ms,
            min_duration_ms=4000,
            max_duration_ms=12000,
        )

        if not planned_scenes:
            raise RuntimeError(
                f"SRT 中没有规划出有效场景: "
                f"{srt_path}"
            )

        scene = planned_scenes[0]
        for planned in planned_scenes:
            planned.visual["srt"] = str(srt_path)

    generated_image_paths: dict[str, Path] = {}
    if image_provider_name == "sensenova":
        image_provider = SenseNovaImageProvider()
        image_dir = output_dir / ".images"
        generation_report = {
            "provider": "sensenova",
            "model": image_provider.model,
            "size": "1024x576",
            "images": [],
        }
        state["image_generation"] = generation_report
        print(f"Image Provider : sensenova ({image_provider.model})")
        for index, planned in enumerate(planned_scenes, start=1):
            _stage("image_generation")
            image_target = image_dir / f"scene-{index:02d}.png"
            image_prompt = build_scene_image_prompt(planned)
            reused = False
            if image_target.is_file():
                from image_generation.sensenova import _validate_png

                try:
                    _validate_png(image_target)
                    reused = True
                    print(f"[resume] reuse image: {image_target}")
                except (OSError, ValueError, RuntimeError):
                    pass
            if not reused:
                image_provider.generate(image_prompt, image_target)
            generated_image_paths[planned.id] = image_target
            planned.renderer = "cinematic"
            planned.visual["image"] = str(image_target)
            planned.visual["show_title"] = False
            generation_report["images"].append({
                "scene_id": planned.id,
                "path": str(image_target),
                "prompt": image_prompt,
                "width": 1024,
                "height": 576,
                "size_bytes": image_target.stat().st_size,
                "reused": reused,
            })
        scene = planned_scenes[0]

    if image_path is not None:
        scene_payload = scene.to_dict()
    else:
        scene_payload = [
            planned.to_dict()
            for planned in planned_scenes
        ]

    scene_json.write_text(
        json.dumps(
            scene_payload,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    print(
        f"[ok] SRT → scene: "
        f"{len(subtitles)} subtitles"
    )

    selected_renderer_name = (
        "cinematic"
        if image_provider_name == "sensenova"
        else select_renderer(scene) if renderer_name == "auto" else renderer_name
    )
    print(f"Renderer : {selected_renderer_name}")

    if image_path is not None:
        annotation = (
            output_dir
            / f"{image_path.stem}.annotation.json"
        )

        run([
            str(env_py),
            str(
                ROOT
                / "scripts"
                / "auto_annotation.py"
            ),
            str(image_path),
            str(srt_path),
            str(annotation)
        ])

        preview = (
            output_dir
            / f"{image_path.stem}-preview.png"
        )

        run([
            str(env_py),
            str(
                ROOT
                / "scripts"
                / "render_annotation_preview.py"
            ),
            str(image_path),
            str(annotation),
            str(preview)
        ])

        print(
            f"[ok] preview: {preview}"
        )

        render_scene = replace(
            scene,
            renderer=selected_renderer_name,
            visual={
                "image": str(image_path),
                "annotation": str(annotation),
            },
        )

        whiteboard_video = (
            output_dir
            / f"{image_path.stem}-whiteboard.mp4"
        )

        state["renderer"] = selected_renderer_name

        if render_only:
            # render_only 无音频，仍按 SRT 末尾推导时长渲染。
            _stage("render")
            render_only_end_ms = max(
                (_subtitle_end_ms(sub) for sub in subtitles),
                default=0,
            )
            render_only_ms = plan_render_duration_ms(
                [scene],
                final_end_ms=render_only_end_ms,
            )
            render_single_scene_on_timeline(
                selected_renderer_name,
                render_scene,
                whiteboard_video,
                render_only_ms[0] if render_only_ms else render_scene.duration_ms,
                state,
            )
            state["render_durations"] = list(render_only_ms)
            print(
                f"[ok] whiteboard: "
                f"{whiteboard_video}"
            )
            print(f"FINAL MP4  : {whiteboard_video}")
            state["final_mp4"] = str(whiteboard_video)
            state["render_video"] = str(whiteboard_video)
            return whiteboard_video

        print()
        print(f"TTS Provider : {tts_name}")
        print(f"Voice : {getattr(provider, 'voice', TTS_VOICE)}")
        print(f"Rate  : {TTS_RATE}")
        print(f"Pitch : {_tts_pitch_label(provider)}")

        _stage("tts")
        provider = _create_tts_provider(tts_name)
        state["tts"] = _tts_diagnostics(tts_name, provider)

        audio_files = generate_all_tts(
            provider,
            subtitles,
            output_dir / ".tts"
        )
        state["tts"] = _tts_diagnostics(tts_name, provider)

        audio_track = (
            output_dir
            / f"{image_path.stem}-voice.m4a"
        )

        state["audio_files"] = [dict(item, file=str(item["file"])) for item in audio_files]
        state["audio_track"] = str(audio_track)
        _stage("audio_timeline")
        final_timeline, timed_srt = prepare_final_timeline(
            output_dir, audio_files, subtitles, state
        )
        # M5-2R：单图分支必须用最终时间轴渲染画面，
        # 避免音频按调整后时间轴、画面仍按原始 SRT，尾部靠 tpad 克隆末帧补齐。
        render_timeline_map = {
            item["id"]: item for item in final_timeline["entries"]
        }
        single_render_ms = plan_render_duration_ms(
            [scene],
            final_end_ms=int(final_timeline.get("final_end_ms") or 0),
            timeline_by_id=render_timeline_map,
        )
        single_target_ms = (
            single_render_ms[0] if single_render_ms else render_scene.duration_ms
        )
        state["render_durations"] = list(single_render_ms)
        _stage("render")
        render_single_scene_on_timeline(
            selected_renderer_name,
            render_scene,
            whiteboard_video,
            single_target_ms,
            state,
        )
        print(
            f"[ok] whiteboard: "
            f"{whiteboard_video}"
        )

        burnin_srt = timed_srt or srt_path
        _stage("audio_mix")
        build_audio_track(
            audio_files,
            audio_track,
            final_timeline=final_timeline,
        )

        print(
            f"[ok] audio: "
            f"{audio_track}"
        )

        _stage("final_merge")
        final_video = (
            output_dir
            / f"{image_path.stem}-final.mp4"
        )
        state["final_mp4"] = str(final_video)
        state["render_video"] = str(whiteboard_video)

        build_final_video(
            whiteboard_video,
            audio_track,
            burnin_srt,
            final_video
        )

        print(
            f"FINAL MP4  : {final_video}"
        )

        return final_video

    cinematic_mode = selected_renderer_name == "cinematic"
    renderer_preference = "cinematic" if cinematic_mode else None
    representation = resolve_visual_representation(
        scene,
        asset_roots=asset_roots or [],
        renderer_preference=renderer_preference,
        explicit_image=(background_map or {}).get(scene.id)
        if background_map and scene.id in background_map
        else generated_image_paths.get(scene.id),
    )
    # Phase 1B: the unified per-scene loop below handles local_asset scenes by
    # running the annotation bridge per scene. Route whiteboard multi-scene
    # inputs through that loop as well so no planned scene is silently skipped.
    whiteboard_multiscene = (
        selected_renderer_name == "whiteboard"
        and image_path is None
        and len(planned_scenes) > 1
        and representation.kind == "local_asset"
    )
    if (
        representation.kind == "procedural_svg"
        or cinematic_mode
        or whiteboard_multiscene
    ):
        selected_renderer_name = representation.renderer
        render_video = (
            output_dir
            / f"{output_dir.name}-render.mp4"
        )
        scenes_to_render = (
            planned_scenes
            if image_path is None
            else [scene]
        )
        final_subtitle_end_ms = max(
            _subtitle_end_ms(sub)
            for sub in subtitles
        ) if subtitles else None
        audio_track = (
            output_dir
            / f"{output_dir.name}-voice.m4a"
        )
        audio_duration_ms = None
        final_timeline = None
        burnin_srt = srt_path
        if not render_only:
            _stage("tts")
            provider = _create_tts_provider(tts_name)
            state["tts"] = _tts_diagnostics(tts_name, provider)
            print(f"TTS Provider : {tts_name}")
            print(f"Voice : {getattr(provider, 'voice', TTS_VOICE)}")
            print(f"Rate  : {TTS_RATE}")
            print(f"Pitch : {_tts_pitch_label(provider)}")
            audio_files = generate_all_tts(
                provider,
                subtitles,
                output_dir / ".tts"
            )
            state["tts"] = _tts_diagnostics(tts_name, provider)
            state["audio_files"] = [dict(item, file=str(item["file"])) for item in audio_files]
            state["audio_track"] = str(audio_track)
            _stage("audio_timeline")
            final_timeline, timed_srt = prepare_final_timeline(
                output_dir, audio_files, subtitles, state
            )
            if timed_srt is not None:
                burnin_srt = timed_srt
            _stage("audio_mix")
            build_audio_track(
                audio_files,
                audio_track,
                final_timeline=final_timeline,
            )
            print(
                f"[ok] audio: {audio_track}"
            )
            audio_duration_probe = ffprobe_duration_ms(audio_track) if audio_track.exists() else None
            audio_duration_ms = int(round(audio_duration_probe * 1000)) if audio_duration_probe else None
        # M5-2：渲染时长与字幕/音频共用最终时间基准
        timeline_map = (
            {item["id"]: item for item in final_timeline["entries"]}
            if final_timeline
            else None
        )
        if final_timeline:
            final_subtitle_end_ms = max(
                final_subtitle_end_ms or 0,
                int(final_timeline["final_end_ms"]),
            )
        render_durations = plan_render_duration_ms(
            scenes_to_render,
            final_end_ms=final_subtitle_end_ms,
            audio_duration_ms=audio_duration_ms,
            timeline_by_id=timeline_map,
        )
        _stage("render")
        state["render_durations"] = list(render_durations)
        scene_clips: list[Path] = []
        for index, (scene_item, render_duration_ms) in enumerate(
            zip(scenes_to_render, render_durations),
            start=1,
        ):
            item_rep = resolve_visual_representation(
                scene_item,
                asset_roots=asset_roots or [],
                renderer_preference=renderer_preference,
                explicit_image=(background_map or {}).get(scene_item.id)
                if background_map and scene_item.id in background_map
                else generated_image_paths.get(scene_item.id),
            )
            item_renderer = item_rep.renderer
            item_payload = dict(item_rep.payload)
            if scene_item.id in generated_image_paths:
                # The generated artwork has no embedded lettering and the
                # spoken narration is already subtitled at the bottom.
                item_payload["show_title"] = False
            # Phase 1B: local_asset scenes resolved by the unified loop carry only
            # the image path; the whiteboard renderer additionally requires an
            # annotation file. Produce it here using the existing bridge so the
            # renderer never receives a payload missing this field. This is the
            # per-scene equivalent of the legacy single-scene whiteboard branch.
            if item_rep.kind == "local_asset" and "annotation" not in item_payload:
                resolution = resolve_assets(scene_item, asset_roots=asset_roots or [])
                bridge_input = auto_annotate_bridge(scene_item, resolution)
                bridge_output = bridge_whiteboard(bridge_input.scene)
                bridge_visual = dict(bridge_output["scene"].get("visual", {}))
                # Preserve extra payload keys (subject/environment) while
                # installing the bridge-generated image and annotation paths.
                item_payload.update(bridge_visual)
                if not item_payload.get("image"):
                    item_payload["image"] = str(bridge_input.scene.visual.get("image", ""))
            render_scene = replace(
                scene_item,
                renderer=item_renderer,
                visual=item_payload,
                duration_ms=render_duration_ms,
            )
            clip_path = (
                output_dir
                / f"{output_dir.name}-scene-{index:02d}.mp4"
            )
            renderer_kwargs = {}
            if item_renderer == "whiteboard":
                # M4-1: 把流水线计算出的有效场景时长（含前置 gap 与末尾对齐）
                # 作为白板渲染的目标时长，避免拼接后累计时间漂移。
                renderer_kwargs["fit_scene_duration"] = True
            renderer = create_renderer(
                item_renderer,
                **renderer_kwargs
            )
            renderer.render(
                render_scene,
                clip_path,
            )
            if item_renderer == "whiteboard":
                # M4-3：白板渲染的实际时长可能因书写编排下限超过目标时长，
                # 这里按目标时长归一，保证拼接后整片长度与 SRT 时间轴一致。
                fit_record = fit_clip_duration(
                    clip_path,
                    render_duration_ms,
                    label=f"scene-{index:02d}",
                    renderer_timing=getattr(renderer, "last_timing_plan", None),
                )
                if fit_record:
                    state.setdefault("clip_fit", []).append(fit_record)
                _collect_timing_artifact(state, renderer)
            scene_clips.append(clip_path)
            state["scene_clips"].append(str(clip_path))
            state["renderer"] = item_renderer
            print(
                f"[ok] scene-{index:02d}: {clip_path}"
            )
        if len(scene_clips) == 1:
            shutil.copyfile(scene_clips[0], render_video)
        else:
            merge_scenes_main(
                [
                    "--inputs",
                    *[str(clip) for clip in scene_clips],
                    "--output",
                    str(render_video),
                ]
            )
        print(
            f"[ok] render: {render_video}"
        )

        if render_only:
            print(f"FINAL MP4  : {render_video}")
            state["final_mp4"] = str(render_video)
            state["render_video"] = str(render_video)
            return render_video

        _stage("final_merge")
        final_video = (
            output_dir
            / f"{output_dir.name}-final.mp4"
        )
        state["final_mp4"] = str(final_video)
        state["render_video"] = str(render_video)
        build_final_video(
            render_video,
            audio_track,
            burnin_srt,
            final_video,
            force_style=CINEMATIC_SUBTITLE_STYLE if cinematic_mode else None,
        )
        print(f"FINAL MP4  : {final_video}")
        return

    if selected_renderer_name != "whiteboard":
        raise RuntimeError(
            f"Unified asset pipeline currently supports whiteboard renderer only; selected={selected_renderer_name}"
        )

    roots = [Path(root) for root in (asset_roots or [])]
    resolution = resolve_assets(scene, asset_roots=roots)
    bridge_input = auto_annotate_bridge(scene, resolution)
    bridge_output = bridge_whiteboard(bridge_input.scene)

    render_scene = Scene.from_dict(bridge_output["scene"])
    render_scene.id = scene.id
    render_scene.narration = scene.narration
    render_scene.subtitles = scene.subtitles
    # M4-1 / M5-2R：单场景白板分支必须用流水线计算的有效场景时长渲染。
    # 有音频时以最终时间轴为准（见下方 prepare_final_timeline 之后），
    # 避免用原始 SRT 渲染后再靠 tpad 克隆末帧补齐。
    single_end_ms = max(
        (_subtitle_end_ms(sub) for sub in subtitles),
        default=None,
    )
    state["renderer"] = selected_renderer_name

    whiteboard_video = (
        output_dir
        / f"{output_dir.name}-whiteboard.mp4"
    )

    if render_only:
        # render_only 无音频，仍按 SRT 末尾推导时长渲染。
        single_render_ms = plan_render_duration_ms(
            [scene],
            final_end_ms=single_end_ms,
        )
        state["render_durations"] = list(single_render_ms)
        _stage("render")
        render_single_scene_on_timeline(
            selected_renderer_name,
            render_scene,
            whiteboard_video,
            single_render_ms[0] if single_render_ms else scene.duration_ms,
            state,
        )
        print(
            f"[ok] whiteboard: "
            f"{whiteboard_video}"
        )
        print(f"FINAL MP4  : {whiteboard_video}")
        state["final_mp4"] = str(whiteboard_video)
        state["render_video"] = str(whiteboard_video)
        return whiteboard_video

    audio_track = (
        output_dir
        / f"{output_dir.name}-voice.m4a"
    )
    _stage("tts")
    provider = _create_tts_provider(tts_name)
    state["tts"] = _tts_diagnostics(tts_name, provider)

    print(f"TTS Provider : {tts_name}")
    print(f"Voice : {getattr(provider, 'voice', TTS_VOICE)}")
    print(f"Rate  : {TTS_RATE}")
    print(f"Pitch : {_tts_pitch_label(provider)}")

    audio_files = generate_all_tts(
        provider,
        subtitles,
        output_dir / ".tts"
    )
    state["tts"] = _tts_diagnostics(tts_name, provider)
    state["audio_files"] = [dict(item, file=str(item["file"])) for item in audio_files]
    state["audio_track"] = str(audio_track)
    _stage("audio_timeline")
    final_timeline, timed_srt = prepare_final_timeline(
        output_dir, audio_files, subtitles, state
    )
    # M5-2R：画面、字幕与音频共用最终时间轴，而非原始 SRT。
    render_timeline_map = {
        item["id"]: item for item in final_timeline["entries"]
    }
    single_render_ms = plan_render_duration_ms(
        [scene],
        final_end_ms=int(final_timeline.get("final_end_ms") or 0),
        timeline_by_id=render_timeline_map,
    )
    state["render_durations"] = list(single_render_ms)
    _stage("render")
    render_single_scene_on_timeline(
        selected_renderer_name,
        render_scene,
        whiteboard_video,
        single_render_ms[0] if single_render_ms else scene.duration_ms,
        state,
    )
    state["render_video"] = str(whiteboard_video)
    print(
        f"[ok] whiteboard: "
        f"{whiteboard_video}"
    )

    burnin_srt = timed_srt or srt_path
    _stage("audio_mix")
    build_audio_track(
        audio_files,
        audio_track,
        final_timeline=final_timeline,
    )

    print(
        f"[ok] audio: "
        f"{audio_track}"
    )

    _stage("final_merge")
    final_video = (
        output_dir
        / f"{output_dir.name}-final.mp4"
    )
    state["final_mp4"] = str(final_video)
    build_final_video(
        whiteboard_video,
        audio_track,
        burnin_srt,
        final_video
    )

    print(f"FINAL MP4  : {final_video}")
    state["final_mp4"] = str(final_video)
    return final_video


# ============================================================
# M4-3 运行包装：产物清单、失败诊断与最终媒体事实
# ============================================================

def _media_facts(path):
    """收集单个媒体/产物文件的事实；无法探测的字段标记为 None。"""
    target = Path(path)
    facts = {
        "path": str(target),
        "exists": target.exists(),
        "size_bytes": None,
        "size_mb": None,
        "duration_ms": None,
        "video_stream_ms": None,
        "audio_stream_ms": None,
    }
    if not facts["exists"]:
        return facts
    try:
        facts["size_bytes"] = target.stat().st_size
        facts["size_mb"] = round(facts["size_bytes"] / 1048576, 3)
    except OSError:
        pass
    try:
        seconds = ffprobe_duration_ms(target)
        facts["duration_ms"] = int(round(seconds * 1000)) if seconds else None
    except Exception:
        facts["duration_ms"] = None
    try:
        facts["video_stream_ms"] = ffprobe_stream_duration_ms(target, "v:0")
    except Exception:
        facts["video_stream_ms"] = None
    try:
        facts["audio_stream_ms"] = ffprobe_stream_duration_ms(target, "a:0")
    except Exception:
        facts["audio_stream_ms"] = None
    return facts


def _assert_outputs_do_not_overwrite_inputs(output_dir, srt_path, image_path=None):
    """拒绝把派生产物写到输入文件上（最小输入保护，不改变既有命名）。"""
    inputs = {Path(srt_path).resolve()}
    if image_path:
        inputs.add(Path(image_path).resolve())
    name = output_dir.name
    derived = {
        (output_dir / "scene.json").resolve(),
        (output_dir / f"{name}-render.mp4").resolve(),
        (output_dir / f"{name}-whiteboard.mp4").resolve(),
        (output_dir / f"{name}-final.mp4").resolve(),
        (output_dir / f"{name}-voice.m4a").resolve(),
    }
    collisions = sorted(str(item) for item in inputs & derived)
    if collisions:
        raise RuntimeError(
            "拒绝执行：本次运行的输出路径会覆盖输入文件 -> "
            + ", ".join(collisions)
            + "；请用 --output 指定其他目录"
        )


# M4-6：本次运行会写入的目标产物（用于覆盖保护）。
# 只覆盖"本次运行确实会写"的固定命名产物，目录中的其他既有文件不参与判定，
# 避免用户把成品放进已有目录时被无关文件误拒。
def _target_artifact_paths(
    output_dir, image_path=None, render_only=False, image_provider_name="none"
):
    """返回本次运行会写入的目标产物绝对路径列表（不含 .tts/.timing 中间目录）。"""
    output_dir = Path(output_dir)
    name = output_dir.name
    targets = [
        output_dir / "scene.json",
        output_dir / f"{name}-render.mp4",
        output_dir / f"{name}-whiteboard.mp4",
        output_dir / "review" / "contact-sheet.jpg",
        output_dir / "review" / "report.json",
    ]
    if image_path is not None:
        targets.append(output_dir / f"{Path(image_path).stem}.annotation.json")
    if not render_only:
        targets += [
            output_dir / f"{name}-voice.m4a",
            output_dir / f"{name}-final.mp4",
            # M5-2：最终时间轴字幕属本次运行产物，同样不得静默覆盖
            output_dir / f"{name}-timed.srt",
        ]
    targets.extend(sorted(output_dir.glob(f"{name}-scene-*.mp4")))
    targets.append(output_dir / "run-manifest.json")
    if image_provider_name == "sensenova":
        targets.append(output_dir / ".images")
    return targets


def _assert_targets_do_not_exist(
    output_dir, image_path=None, render_only=False, image_provider_name="none", resume=False
):
    """M4-6：拒绝静默覆盖已存在的本次运行目标产物。

    契约：
    - 检查发生在任何可能破坏旧成品的写入之前（main 内、_run_pipeline 之前）；
    - 冲突时抛错并列出全部冲突路径，旧文件一个字节都不改；
    - 绝不采用"先删旧再生成"的方式；
    - 保护范围覆盖本次运行会写的全部目标产物（MP4 / 音频 / scene.json / manifest /
      annotation），以及 .tts / .timing 中间产物目录中的既有内容；
    - 目录中与本次运行无关的文件不参与冲突判定。
    """
    conflicts = [path for path in _target_artifact_paths(
        output_dir,
        image_path=image_path,
        render_only=render_only,
        image_provider_name=image_provider_name,
    ) if path.exists() and not (
        resume
        and image_provider_name == "sensenova"
        and path == Path(output_dir) / "run-manifest.json"
    ) and not (
        resume
        and image_provider_name == "sensenova"
        and path == Path(output_dir) / ".images"
    )]

    output_dir = Path(output_dir)
    protected_intermediate_dirs = [".tts", ".timing"]
    if image_provider_name == "sensenova" and not resume:
        protected_intermediate_dirs.append(".images")
    for sub in protected_intermediate_dirs:
        intermediate_dir = output_dir / sub
        if intermediate_dir.is_dir():
            existing = sorted(str(item) for item in intermediate_dir.iterdir())
            if existing:
                conflicts.append(intermediate_dir)

    if conflicts:
        raise RuntimeError(
            "拒绝覆盖：本次运行的目标产物已存在 -> "
            + ", ".join(sorted({str(item) for item in conflicts}))
            + "；请用 --output 指定新的输出目录，或先手动移走旧产物"
        )


def _collect_timing_artifact(state, renderer):
    """M4-6：记录渲染器本次真实写入的 timing 诊断文件路径（不复制内容）。"""
    timing_path = getattr(renderer, "last_fit_path", None)
    if not timing_path:
        return
    recorded = state.setdefault("timing_files", [])
    value = str(timing_path)
    if value not in recorded:
        recorded.append(value)


def _timing_diagnostics_entries(output_dir, state):
    """把本次真实生成且仍存在的 timing 文件转成相对输出目录的引用。"""
    entries = []
    base = Path(output_dir).resolve()
    for raw in state.get("timing_files") or []:
        path = Path(raw)
        if not path.exists():
            continue
        try:
            relative = path.resolve().relative_to(base).as_posix()
        except ValueError:
            continue
        entries.append({
            "path": relative,
            "exists": True,
            "size_bytes": path.stat().st_size,
        })
    return entries


def _build_audio_timeline_report(output_dir, state):
    """复算语音时间轴诊断（只读 ffprobe），失败时标记 unknown 而不中断。"""
    audio_files = state.get("audio_files") or []
    if not audio_files:
        return None
    try:
        entries = build_audio_timeline(audio_files)
        srt_end_ms = max(
            (entry.get("planned_end_ms") or 0) for entry in entries
        ) if entries else None
        report = summarize_audio_timeline(entries, srt_end_ms=srt_end_ms)
        # M5-2：manifest 同时保留调整前（entries）与调整后（final_timeline）
        final_timeline = state.get("final_timeline")
        if final_timeline is None:
            final_timeline = build_final_timeline(entries)
        report["final_timeline"] = final_timeline
        report["timeline_adjusted"] = bool(final_timeline.get("adjusted_cues"))
        report["srt"] = state.get("srt")
        try:
            (output_dir / "audio-timeline.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            print(f"[WARN] audio-timeline.json 写入失败: {exc}")
        return report
    except Exception as exc:  # ffprobe 不可用或素材非法
        return {"status": "unknown", "error": str(exc)}


def _assess_final_media(
    final_facts,
    *,
    audio_expected,
    scene_coverage=None,
    scene_semantics=None,
    timeline_conflicts=None,
    visual_review=None,
):
    """Return a small machine-readable check for the final MP4 container."""
    findings = []
    if not final_facts or not final_facts.get("exists"):
        return {"status": "failed", "findings": ["final_video_missing"]}
    if not final_facts.get("size_bytes"):
        findings.append("final_video_empty")
    if not final_facts.get("video_stream_ms"):
        findings.append("video_stream_missing_or_unreadable")
    if audio_expected and not final_facts.get("audio_stream_ms"):
        findings.append("audio_stream_missing_or_unreadable")
    if scene_coverage and scene_coverage.get("status") == "failed":
        findings.append("scene_subtitle_coverage_failed")

    if findings:
        return {"status": "failed", "findings": findings}

    review_findings = []
    if audio_expected and final_facts.get("end_delta_within_200ms") is False:
        review_findings.append("audio_video_end_delta_over_200ms")
    if timeline_conflicts:
        review_findings.append("audio_timeline_conflicts")
    if scene_semantics and scene_semantics.get("status") == "review_required":
        review_findings.append("scene_semantics_incomplete")
    if visual_review and visual_review.get("status") == "unavailable":
        review_findings.append("visual_review_unavailable")
    if visual_review and visual_review.get("findings"):
        review_findings.append("visual_anomaly_detected")
    if review_findings:
        return {
            "status": "review_required",
            "findings": review_findings,
        }
    return {"status": "passed", "findings": []}


def _generate_visual_review(final_mp4, output_dir, duration_ms):
    """Create a contact sheet and flag sampled black or near-uniform frames."""
    if not final_mp4 or not Path(final_mp4).is_file():
        return {"status": "unavailable", "findings": ["final_video_missing"]}
    if not duration_ms or duration_ms <= 0:
        return {"status": "unavailable", "findings": ["video_duration_unknown"]}

    try:
        from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageStat

        output_dir = Path(output_dir)
        review_dir = output_dir / "review"
        review_dir.mkdir(parents=True, exist_ok=True)
        sheet_path = review_dir / "contact-sheet.jpg"
        report_path = review_dir / "report.json"
        sample_ms = sorted({
            max(0, min(duration_ms - 1, int(duration_ms * fraction)))
            for fraction in (0.05, 0.25, 0.5, 0.75, 0.9)
        })
        columns = 3
        cell_width, cell_height, label_height, margin = 480, 270, 32, 16
        rows = (len(sample_ms) + columns - 1) // columns
        sheet = Image.new(
            "RGB",
            (margin * 2 + columns * cell_width, margin * 2 + rows * (cell_height + label_height)),
            "#eeeeee",
        )
        draw = ImageDraw.Draw(sheet)
        font = ImageFont.load_default()
        frame_reports = []

        with tempfile.TemporaryDirectory(prefix=".review-frames-", dir=str(output_dir)) as temp_dir:
            for index, timestamp_ms in enumerate(sample_ms):
                frame_path = Path(temp_dir) / f"frame-{index:02d}.png"
                extract = subprocess.run(
                    [
                        "ffmpeg", "-nostdin", "-y", "-v", "error",
                        "-ss", f"{timestamp_ms / 1000:.3f}", "-i", str(final_mp4),
                        "-frames:v", "1", str(frame_path),
                    ],
                    capture_output=True,
                    text=True,
                )
                if extract.returncode != 0 or not frame_path.is_file():
                    detail = extract.stderr.strip() or "frame extraction produced no image"
                    raise RuntimeError(f"Could not extract frame at {timestamp_ms}ms: {detail}")

                with Image.open(frame_path) as source:
                    frame = source.convert("RGB")
                gray_stats = ImageStat.Stat(frame.convert("L"))
                mean_luma = round(gray_stats.mean[0], 2)
                contrast = round(gray_stats.stddev[0], 2)
                flags = []
                if mean_luma < 8:
                    flags.append("near_black")
                if contrast < 2.5:
                    flags.append("near_uniform")

                column = index % columns
                row = index // columns
                x = margin + column * cell_width
                y = margin + row * (cell_height + label_height)
                preview = ImageOps.contain(frame, (cell_width, cell_height))
                sheet.paste(preview, (x + (cell_width - preview.width) // 2, y))
                draw.text((x, y + cell_height + 6), f"{timestamp_ms / 1000:.2f}s", fill="#222222", font=font)
                frame_reports.append({
                    "timestamp_ms": timestamp_ms,
                    "mean_luma": mean_luma,
                    "contrast": contrast,
                    "findings": flags,
                })

        sheet.save(sheet_path, format="JPEG", quality=88, optimize=True)
        findings = [
            f"frame_at_{frame['timestamp_ms']}ms:{flag}"
            for frame in frame_reports
            for flag in frame["findings"]
        ]
        report = {
            "status": "review_required" if findings else "sampled",
            "contact_sheet": str(sheet_path),
            "sampled_frames": frame_reports,
            "findings": findings,
            "scope": "Flags sampled near-black or near-uniform frames; does not assess semantics, continuity, or aesthetics.",
        }
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return report
    except Exception as exc:
        return {
            "status": "unavailable",
            "findings": [],
            "error": f"{type(exc).__name__}: {exc}",
        }


def _inspect_scene_coverage(scene_json_path, expected_subtitle_ids):
    """Check that scene.json preserves every SRT cue exactly once."""
    if not scene_json_path:
        return {"status": "skipped", "findings": ["scene_json_not_recorded"]}
    path = Path(scene_json_path)
    if not path.is_file():
        return {"status": "failed", "findings": ["scene_json_missing"]}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {
            "status": "failed",
            "findings": ["scene_json_unreadable"],
            "error": str(exc),
        }

    scenes = payload if isinstance(payload, list) else [payload]
    findings = []
    cue_ids = []
    if not scenes or any(not isinstance(scene, dict) for scene in scenes):
        return {"status": "failed", "findings": ["scene_json_shape_invalid"]}

    for index, scene in enumerate(scenes, start=1):
        subtitles = scene.get("subtitles")
        if not isinstance(subtitles, list):
            findings.append(f"scene_{index}_subtitles_missing")
            continue
        for subtitle in subtitles:
            if not isinstance(subtitle, dict) or subtitle.get("id") is None:
                findings.append(f"scene_{index}_subtitle_id_missing")
                continue
            cue_ids.append(str(subtitle["id"]))

    expected = list(expected_subtitle_ids or [])
    duplicates = sorted(cue_id for cue_id, count in Counter(cue_ids).items() if count > 1)
    missing = sorted(set(expected) - set(cue_ids))
    unexpected = sorted(set(cue_ids) - set(expected))
    if duplicates:
        findings.append("duplicate_subtitle_ids:" + ",".join(duplicates))
    if missing:
        findings.append("missing_subtitle_ids:" + ",".join(missing))
    if unexpected:
        findings.append("unexpected_subtitle_ids:" + ",".join(unexpected))

    return {
        "status": "failed" if findings else "passed",
        "scene_count": len(scenes),
        "expected_subtitle_count": len(expected),
        "actual_subtitle_count": len(cue_ids),
        "findings": findings,
    }


def _inspect_scene_semantics(scene_json_path):
    """Flag scenes whose effective visual subject is still the generic fallback."""
    if not scene_json_path:
        return {"status": "skipped", "findings": ["scene_json_not_recorded"]}
    path = Path(scene_json_path)
    if not path.is_file():
        return {"status": "unavailable", "findings": ["scene_json_missing"]}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {
            "status": "unavailable",
            "findings": ["scene_json_unreadable"],
            "error": str(exc),
        }

    scenes = payload if isinstance(payload, list) else [payload]
    if not scenes or any(not isinstance(scene, dict) for scene in scenes):
        return {"status": "unavailable", "findings": ["scene_json_shape_invalid"]}

    findings = []
    scene_reports = []
    for index, scene in enumerate(scenes, start=1):
        visual = scene.get("visual") or {}
        if not isinstance(visual, dict):
            visual = {}
        plan = visual.get("visual_plan") or {}
        if not isinstance(plan, dict):
            plan = {}
        semantic = plan.get("semantic") or visual.get("semantic") or {}
        if not isinstance(semantic, dict):
            semantic = {}
        subject = plan.get("subject")
        if not subject or subject == "scene":
            subject = semantic.get("subject") or visual.get("subject") or "scene"
        item = {
            "scene": scene.get("sceneId") or scene.get("id") or f"scene-{index:02d}",
            "subject": str(subject),
            "intent": visual.get("intent"),
        }
        if item["subject"] == "scene":
            item["finding"] = "generic_subject"
            findings.append(item)
        scene_reports.append(item)

    return {
        "status": "review_required" if findings else "passed",
        "scene_count": len(scenes),
        "generic_scene_count": len(findings),
        "scenes": scene_reports,
        "findings": findings,
        "scope": "Flags generic scene subjects; does not judge whether a concrete subject is visually accurate.",
    }


def _write_run_diagnostics(output_dir, state, *, status, started_at, error=None):
    """把本次运行的状态、产物清单与媒体事实写入 run-manifest.json。"""
    if output_dir is None:
        return
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return

    audio_timeline = _build_audio_timeline_report(output_dir, state)
    final_mp4 = state.get("final_mp4")
    final_facts = _media_facts(final_mp4) if final_mp4 else None
    scene_coverage = _inspect_scene_coverage(
        state.get("scene_json"),
        state.get("subtitle_ids"),
    )
    scene_semantics = _inspect_scene_semantics(state.get("scene_json"))
    visual_review = (
        _generate_visual_review(final_mp4, output_dir, final_facts.get("duration_ms"))
        if status == "success" and final_facts
        else {"status": "unavailable", "findings": [], "reason": "pipeline_failed"}
    )
    if final_facts and final_facts.get("video_stream_ms") and final_facts.get("audio_stream_ms"):
        delta = abs(final_facts["audio_stream_ms"] - final_facts["video_stream_ms"])
        final_facts["end_delta_ms"] = delta
        final_facts["end_delta_within_200ms"] = delta <= 200
        if delta > 200:
            print(
                f"[final][WARN] 音画结束差 {delta}ms 超过 200ms MVP 容差，"
                "详见 run-manifest.json"
            )
    elif final_facts is not None:
        final_facts["end_delta_ms"] = None
        final_facts["end_delta_within_200ms"] = None

    artifact_paths = []
    for key in ("scene_json", "srt", "timed_srt", "audio_track",
                "render_video", "final_mp4"):
        value = state.get(key)
        if value:
            artifact_paths.append(value)
    artifact_paths.extend(state.get("scene_clips") or [])

    manifest = {
        "phase": "M4-3",
        "status": status,
        "started_at": started_at,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "output_dir": str(output_dir),
        "stage_reached": state.get("stage"),
        "renderer": state.get("renderer"),
        "tts": state.get("tts"),
        "image_generation": state.get("image_generation", {
            "provider": "procedural_or_local",
            "images": [],
        }),
        "quality_check": _assess_final_media(
            final_facts,
            audio_expected=bool(state.get("audio_track")),
            scene_coverage=scene_coverage,
            scene_semantics=scene_semantics,
            timeline_conflicts=(state.get("final_timeline") or {}).get("conflicts"),
            visual_review=visual_review,
        ),
        "scene_coverage": scene_coverage,
        "scene_semantics": scene_semantics,
        "visual_review": visual_review,
        "inputs": {
            "srt": state.get("srt"),
            "image": state.get("image"),
        },
        "outputs": {
            "scene_json": state.get("scene_json"),
            "srt": state.get("srt"),
            "timed_srt": state.get("timed_srt"),
            "audio_track": state.get("audio_track"),
            "render_video": state.get("render_video"),
            "scene_clips": list(state.get("scene_clips") or []),
            "generated_images": [
                item.get("path")
                for item in (state.get("image_generation") or {}).get("images", [])
            ],
            "final_mp4": final_mp4,
        },
        "render_durations_ms": state.get("render_durations"),
        "timeline_diagnostics": {
            "status": (state.get("final_timeline") or {}).get("status"),
            "timeline_blocked": bool(state.get("timeline_blocked")),
            "blocking_conflicts": list(state.get("blocking_conflicts") or []),
            "warnings": list((state.get("final_timeline") or {}).get("warnings") or []),
            "conflicts": list((state.get("final_timeline") or {}).get("conflicts") or []),
            "total_speech_overrun_ms": (
                state.get("final_timeline") or {}
            ).get("total_speech_overrun_ms"),
            "total_displacement_ms": (
                state.get("final_timeline") or {}
            ).get("total_displacement_ms"),
        },
        "clip_fit": list(state.get("clip_fit") or []),
        "timing_diagnostics": _timing_diagnostics_entries(output_dir, state),
        "artifacts": [_media_facts(item) for item in artifact_paths],
        "final_media": final_facts,
        "audio_timeline": audio_timeline,
        "error": None if error is None else {
            "type": type(error).__name__,
            "message": str(error),
            "stage": state.get("stage"),
        },
    }

    try:
        (output_dir / "run-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[ok] run manifest: {output_dir / 'run-manifest.json'}")
    except OSError as exc:
        print(f"[WARN] run-manifest.json 写入失败: {exc}")


def main(
    srt_path,
    output_dir=None,
    renderer_name="auto",
    tts_name="audio8",
    render_only=False,
    env_py=None,
    asset_roots=None,
    image_path=None,
    background_map=None,
    group_target_ms=8000,
    image_provider_name="none",
    resume=False,
):
    """M4-3 包装层：保持既有调用契约，额外产出产物清单与失败诊断。"""
    if resume and image_provider_name != "sensenova":
        raise ValueError("resume is supported only with the SenseNova image provider")
    started_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    state = {}
    resolved_output = Path(
        output_dir or Path(srt_path).resolve().parent
    ).resolve()

    _assert_outputs_do_not_overwrite_inputs(
        resolved_output,
        srt_path=srt_path,
        image_path=image_path,
    )

    # M4-6：在任何破坏性写入之前拒绝覆盖已存在的目标产物；
    # 放在 try 之外，冲突时不写 run-manifest.json，避免顺带覆盖旧诊断清单。
    _assert_targets_do_not_exist(
        resolved_output,
        image_path=image_path,
        render_only=render_only,
        image_provider_name=image_provider_name,
        resume=resume,
    )

    try:
        result = _run_pipeline(
            srt_path,
            output_dir=output_dir,
            renderer_name=renderer_name,
            tts_name=tts_name,
            render_only=render_only,
            env_py=env_py,
            asset_roots=asset_roots,
            image_path=image_path,
            background_map=background_map,
            group_target_ms=group_target_ms,
            image_provider_name=image_provider_name,
            resume=resume,
            state=state,
        )
        _write_run_diagnostics(
            resolved_output,
            state,
            status="success",
            started_at=started_at,
        )
        return result if result is not None else state.get("final_mp4")
    except BaseException as exc:
        _write_run_diagnostics(
            resolved_output,
            state,
            status="failed",
            started_at=started_at,
            error=exc,
        )
        raise


# ============================================================
# CLI
# ============================================================

def _parse_legacy_positional(argv):
    if len(argv) >= 4 and not argv[0].startswith("--"):
        srt_path, image_path, output_dir = argv[:3]
        rest = argv[3:]
        return srt_path, image_path, output_dir, rest
    return None


def main_cli(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)

    if not args:
        print("用法: python scripts/video_pipeline.py <SRT> [--output DIR] [--renderer NAME] [--tts NAME] [--asset-root PATH] [--render-only]")
        print("兼容旧用法: python scripts/video_pipeline.py <SRT> <IMAGE> <OUTPUT_DIR> [--tts NAME] [--renderer NAME]")
        raise SystemExit(1)

    legacy = _parse_legacy_positional(args)
    if legacy is not None:
        srt_path, image_path, output_dir, rest = legacy
        parser = argparse.ArgumentParser(description="Video pipeline")
        parser.add_argument("srt", help="SRT path")
        parser.add_argument("image", help="Image path")
        parser.add_argument("output_dir", help="Output directory")
        parser.add_argument("--tts", default="audio8")
        parser.add_argument("--renderer", default="whiteboard")
        parsed = parser.parse_args(rest)
        main(parsed.srt, parsed.output_dir, renderer_name=parsed.renderer, tts_name=parsed.tts, image_path=parsed.image)
        return

    parser = argparse.ArgumentParser(description="Video pipeline")
    parser.add_argument("srt", nargs="?", help="SRT path")
    parser.add_argument("--input", dest="input", help="SRT path")
    parser.add_argument("--output", help="Output directory")
    parser.add_argument("--renderer", default="auto", help="Renderer override or auto")
    parser.add_argument(
        "--tts",
        choices=("audio8", "auto", "edge", "espeak"),
        default="audio8",
        help="TTS provider: audio8 (default), auto (Audio8 then eSpeak), edge, or espeak",
    )
    parser.add_argument(
        "--image-provider",
        choices=("none", "sensenova"),
        default="none",
        help="Scene image generation provider (sensenova generates one image per scene)",
    )
    parser.add_argument("--asset-root", action="append", default=[], help="Additional asset root; repeatable")
    parser.add_argument("--env-py", help="Python interpreter path for helper scripts")
    parser.add_argument("--image", help="Optional image path for legacy/manual image mode")
    parser.add_argument("--render-only", action="store_true", help="Render MP4 only; skip TTS/audio merge")
    parser.add_argument("--background-map", help="JSON file mapping scene id -> background image path (cinematic mode)")
    parser.add_argument("--group-target", type=int, default=8000, help="Target scene duration in ms for SRT grouping")
    parser.add_argument("--resume", action="store_true", help="Resume an interrupted SenseNova image run, reusing valid scene images")
    parsed = parser.parse_args(args)

    srt_path = parsed.input or parsed.srt
    if not srt_path:
        print("Missing required SRT input")
        parser.print_help()
        raise SystemExit(1)

    background_map = None
    if parsed.background_map:
        background_map = json.loads(Path(parsed.background_map).read_text(encoding="utf-8"))
    main(
        srt_path,
        parsed.output,
        renderer_name=parsed.renderer,
        tts_name=parsed.tts,
        render_only=parsed.render_only,
        env_py=parsed.env_py,
        asset_roots=parsed.asset_root,
        image_path=parsed.image,
        background_map=background_map,
        group_target_ms=parsed.group_target,
        image_provider_name=parsed.image_provider,
        resume=parsed.resume,
    )


if __name__ == "__main__":
    main_cli()
