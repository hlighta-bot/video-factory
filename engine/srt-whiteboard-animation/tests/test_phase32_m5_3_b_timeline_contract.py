"""M5-3 B：时间轴契约测试的最小整改与独立验证（完全离线，零媒体调用）。

设计原则（对应上一轮 A-R 七项测试质量风险）：
- 异源锚点：期望值由本文件独立手算 `_manual_layout` 推导，绝不复用被测函数
  的返回值反推期望；再用落盘 SRT 文本第三次交叉核对。
- 零替身：全部用例直接调用生产纯函数（build_final_timeline /
  plan_render_duration_ms / _target_artifact_paths /
  _assert_targets_do_not_exist / write_timed_srt），不 mock、不注入。
- 零媒体：不 import ffmpeg/ffprobe 封装，不启动 subprocess，不生成音频/视频，
  因此本文件可在禁止真实媒体工具的受限环境内完整执行。
- 可重跑：全部产物落在每次运行唯一的 WORK_ROOT 下，目录以 exist_ok=False
  创建，不读取、不依赖任何历史测试产物。
- 单一契约：每个 test_* 只验证一个主要契约。

覆盖：U3-1（整改后的异源比较）、U3-3（默认 gap 可达性穷举）、
U4-2（多场景成功路径 · 规划层）、U4-3（render_only 语义 · 规划层 + 静态证据）、
U4-4（覆盖保护 · 内容哈希）、测试有效性元验证、可重跑性。

明确不覆盖（UNVERIFIED，见 M5-3-B-REPORT.md）：
- 多场景端到端渲染与最终容器音画差（需 ffmpeg/ffprobe，本阶段禁止）；
- render_only 端到端执行（需渲染器 + 媒体工具）。

运行：
  PYTHONPATH=scripts .venv/bin/python tests/test_phase32_m5_3_b_timeline_contract.py
"""

from __future__ import annotations

import ast
import hashlib
import os
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
from parse_srt import parse_srt as parse_srt_cues  # noqa: E402
from scene.planner import plan_srt_grouped  # noqa: E402

GAP_MS = 80
WORK_ROOT = Path(
    os.environ.get("M53B_TMP")
    or tempfile.mkdtemp(prefix="m53b-", dir="/tmp")
).resolve()
WORK_ROOT.mkdir(parents=True, exist_ok=True)
# 每次进程运行一个唯一 session 目录：同一个 M53B_TMP 可重复执行，不依赖、
# 也不复用任何历史产物（整改上一轮「不可重跑」风险）。
SESSION_DIR = WORK_ROOT / f"session-{int(time.time() * 1000)}-{os.getpid()}"
SESSION_DIR.mkdir(parents=True, exist_ok=False)


# ============================================================
# 通用工具（全部为本地独立实现，不复用被测逻辑）
# ============================================================

def _case_dir(name):
    """每个用例一个全新目录；目录已存在即失败，杜绝复用历史产物。"""
    path = SESSION_DIR / name
    path.mkdir(parents=True, exist_ok=False)
    if path.exists() is False:  # pragma: no cover - 防御性
        raise AssertionError(f"工作目录创建失败: {path}")
    return path


def _srt_ts_to_ms(value):
    hms, millis = value.strip().split(",")
    hours, minutes, seconds = (int(part) for part in hms.split(":"))
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + int(millis)


def _parse_srt_text_ends(text):
    """独立解析 SRT 文本，返回每条的 (start_ms, end_ms)，不使用 vp 的解析器。"""
    spans = []
    for line in text.splitlines():
        if "-->" not in line:
            continue
        left, right = line.split("-->")
        spans.append((_srt_ts_to_ms(left), _srt_ts_to_ms(right)))
    return spans


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _snapshot(root):
    return {
        str(item.relative_to(root)): _sha256(item)
        for item in sorted(Path(root).rglob("*"))
        if item.is_file()
    }


def _manual_layout(rows, gap_ms):
    """独立手算实现（异源）：rows = [(id, planned_start, planned_end, duration)]。

    只表达契约文字：语音完整优先、顺序不倒退、起点不早于上一条结束 + gap。
    """
    layout = []
    prev_end = None
    for cue_id, planned_start, planned_end, duration in rows:
        final_start = (
            planned_start if prev_end is None
            else max(planned_start, prev_end + gap_ms)
        )
        final_end = final_start + duration
        layout.append({
            "id": cue_id,
            "final_start_ms": final_start,
            "final_end_ms": final_end,
            "shifted_ms": final_start - planned_start,
            "overrun_ms": max(0, final_end - planned_end),
            "subtitle_start_ms": final_start,
            "subtitle_end_ms": max(planned_end, final_end),
        })
        prev_end = final_end
    return layout


def _manual_conflicts(layout, gap_ms):
    """按契约文字独立复算 conflicts（含字幕挤压与 blocking 判定）。"""
    conflicts = []
    for index, item in enumerate(layout):
        if index + 1 < len(layout):
            next_start = layout[index + 1]["subtitle_start_ms"]
            if item["subtitle_end_ms"] > next_start:
                capped = max(item["subtitle_start_ms"], next_start - 1)
                conflicts.append({
                    "id": item["id"],
                    "kind": "subtitle_window_capped",
                    "severity": "warning",
                    "from_ms": item["subtitle_end_ms"],
                    "to_ms": capped,
                })
                item["subtitle_end_ms"] = capped
        if item["subtitle_end_ms"] < item["final_end_ms"]:
            conflicts.append({
                "id": item["id"],
                "kind": "subtitle_shorter_than_speech",
                "severity": "blocking",
            })
    return conflicts


def _assert_equal(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label} 不一致：actual={actual!r} expected={expected!r}")


def _expected_rows_from_scenes(scenes, durations):
    """把场景字幕窗口与给定的实测语音时长拼成独立手算输入。"""
    rows = []
    flat = [sub for scene in scenes for sub in scene.subtitles]
    if len(flat) != len(durations):
        raise AssertionError(
            f"字幕条数 {len(flat)} 与给定语音时长条数 {len(durations)} 不匹配"
        )
    for sub, duration in zip(flat, durations):
        rows.append((
            int(sub["id"]),
            int(sub["start_ms"]),
            int(sub["end_ms"]),
            int(duration),
        ))
    return rows


def _build_scenes(case_dir, cue_windows, group_target_ms=8000):
    """用生产解析器 + 生产分组器构造多场景（不落任何媒体）。"""
    blocks = []
    for index, (start_ms, end_ms) in enumerate(cue_windows, start=1):
        blocks.append(
            f"{index}\n"
            f"{vp._ms_to_srt_timestamp(start_ms)} --> {vp._ms_to_srt_timestamp(end_ms)}\n"
            f"第{index}条旁白文本"
        )
    raw = "\n\n".join(blocks) + "\n"
    srt_path = case_dir / "input.srt"
    srt_path.write_text(raw, encoding="utf-8")
    cues = parse_srt_cues(raw)
    scenes = plan_srt_grouped(
        cues,
        renderer="whiteboard",
        target_duration_ms=group_target_ms,
        min_duration_ms=4000,
        max_duration_ms=12000,
    )
    return srt_path, scenes


# ============================================================
# B1：U3-1 整改 —— 三重异源交叉（独立手算 / 生产时间轴 / 落盘 SRT 文本）
# ============================================================

def test_b1_u3_1_three_way_cross_check():
    """断言链：生产时间轴 == 独立手算；落盘 SRT 末尾 == 独立手算；渲染规划 == 独立手算。

    与旧 U3-1 的差别：旧用例把「timed SRT 末尾」当作期望值再比较同源产出的
    render_durations，属同源比较。此处期望值来自 _manual_layout（独立实现）
    与落盘文本的独立解析，两条链路互不共享代码。
    """
    case_dir = _case_dir("b1")
    rows = [
        (1, 0, 2500, 3200),
        (2, 3000, 5500, 6700),
        (3, 6000, 8500, 2000),
        (4, 9000, 11500, 1500),
    ]
    expected = _manual_layout(rows, GAP_MS)
    expected_conflicts = _manual_conflicts(
        [dict(item) for item in expected], GAP_MS
    )

    entries = [
        {
            "id": cue_id,
            "planned_start_ms": start,
            "planned_end_ms": end,
            "duration_ms": duration,
            "actual_end_ms": start + duration,
        }
        for cue_id, start, end, duration in rows
    ]
    timeline = vp.build_final_timeline(entries, gap_ms=GAP_MS)

    _assert_equal(timeline["final_end_ms"], 13640, "B1 手算 final_end_ms 常量")
    _assert_equal(
        timeline["final_end_ms"], expected[-1]["final_end_ms"],
        "B1 生产 final_end_ms vs 独立手算",
    )
    for got, want in zip(timeline["entries"], expected):
        for key in ("final_start_ms", "final_end_ms", "shifted_ms", "overrun_ms"):
            _assert_equal(got[key], want[key], f"B1 #{want['id']} {key}")
    _assert_equal(
        [item["kind"] for item in timeline["conflicts"]],
        [item["kind"] for item in expected_conflicts],
        "B1 conflicts 种类",
    )
    _assert_equal(timeline["has_blocking_conflicts"], False, "B1 无 blocking")

    # 第三条链路：真实落盘文件 + 本地独立解析器。
    subtitles = [
        {
            "id": cue_id,
            "start_ms": start,
            "end_ms": end,
            "text": f"cue {cue_id}",
        }
        for cue_id, start, end, _duration in rows
    ]
    timed_path = vp.write_timed_srt(timeline, subtitles, case_dir / "out-timed.srt")
    spans = _parse_srt_text_ends(timed_path.read_text(encoding="utf-8"))
    _assert_equal(len(spans), 4, "B1 timed SRT 条数")
    _assert_equal(
        spans[-1][1], expected[-1]["final_end_ms"],
        "B1 落盘 SRT 末尾 vs 独立手算",
    )
    for got, want in zip(spans, expected):
        _assert_equal(got[0], want["subtitle_start_ms"], "B1 落盘 SRT 起点")
        _assert_equal(got[1], want["subtitle_end_ms"], "B1 落盘 SRT 终点")

    # 第四链路：渲染规划必须等于独立手算的最终结尾，而非同源回读。
    scene = vp.Scene(
        id="scene-01",
        renderer="whiteboard",
        visual={},
        subtitles=subtitles,
    )
    planned = vp.plan_render_duration_ms(
        [scene],
        final_end_ms=timeline["final_end_ms"],
        timeline_by_id={item["id"]: item for item in timeline["entries"]},
    )
    _assert_equal(planned, [expected[-1]["final_end_ms"]], "B1 单场景渲染时长")
    print(
        f"[B1] final_end={timeline['final_end_ms']} "
        f"shifted={[item['shifted_ms'] for item in expected]} "
        f"conflicts={[item['kind'] for item in expected_conflicts]}"
    )


# ============================================================
# B2：U4-2 多场景成功路径（规划层，真实生产函数）
# ============================================================

def test_b2_u4_2_multiscene_success_path():
    """多场景 + 纯 warning：时间轴、分组、渲染规划必须全部按独立手算落地。

    代表性：4 cue 分组为 2 场景；第 1 条字幕被下一条起点挤压（warning），
    全程无 blocking，生产应继续；每段渲染时长连续无缝隙，末段终点等于
    final_end_ms（即画面覆盖全片旁白）。
    """
    case_dir = _case_dir("b2")
    windows = [(0, 2500), (2000, 4500), (4200, 6700), (6400, 8900)]
    durations = [1000, 3000, 3000, 3000]
    srt_path, scenes = _build_scenes(case_dir, windows)

    _assert_equal(len(scenes), 2, "B2 场景数")
    _assert_equal([len(scene.subtitles) for scene in scenes], [2, 2], "B2 每场景 cue 数")
    _assert_equal(
        [sub["id"] for scene in scenes for sub in scene.subtitles],
        [1, 2, 3, 4],
        "B2 cue 编号连续性",
    )

    rows = _expected_rows_from_scenes(scenes, durations)
    _assert_equal(len(rows), 4, "B2 手算输入条数")
    expected = _manual_layout(rows, GAP_MS)
    expected_conflicts = _manual_conflicts(
        [dict(item) for item in expected], GAP_MS
    )
    blocking_expected = [
        item for item in expected_conflicts if item["severity"] == "blocking"
    ]
    _assert_equal(blocking_expected, [], "B2 独立手算无 blocking（纯 warning 场景）")
    _assert_equal(
        [item["kind"] for item in expected_conflicts],
        ["subtitle_window_capped"],
        "B2 独立手算冲突种类",
    )

    entries = [
        {
            "id": cue_id,
            "planned_start_ms": start,
            "planned_end_ms": end,
            "duration_ms": duration,
            "actual_end_ms": start + duration,
        }
        for cue_id, start, end, duration in rows
    ]
    timeline = vp.build_final_timeline(entries, gap_ms=GAP_MS)
    _assert_equal(timeline["has_blocking_conflicts"], False, "B2 生产无 blocking")
    _assert_equal(
        [item["kind"] for item in timeline["conflicts"]],
        [item["kind"] for item in expected_conflicts],
        "B2 生产冲突与独立手算一致",
    )
    _assert_equal(
        len(timeline["warnings"]), len(expected_conflicts), "B2 warning 全部保留为诊断"
    )
    _assert_equal(timeline["status"], "conflict", "B2 status 标记")
    _assert_equal(
        [item["final_start_ms"] for item in timeline["entries"]],
        [item["final_start_ms"] for item in expected],
        "B2 最终起点",
    )
    _assert_equal(
        timeline["final_end_ms"], expected[-1]["final_end_ms"], "B2 final_end_ms"
    )

    # 渲染规划：独立手算每个场景的起止，再与生产函数比较。
    scene_windows = []
    for index, scene in enumerate(scenes):
        first = expected[[sub["id"] for sub in scene.subtitles][0] - 1]
        last = expected[[sub["id"] for sub in scene.subtitles][-1] - 1]
        scene_windows.append((first["subtitle_start_ms"], last["subtitle_end_ms"]))
    manual_durations = []
    for index, (start_ms, end_ms) in enumerate(scene_windows):
        if index + 1 < len(scene_windows):
            effective_end = max(scene_windows[index + 1][0], end_ms)
        else:
            effective_end = max(expected[-1]["final_end_ms"], end_ms)
        manual_durations.append(max(1000, effective_end - start_ms))

    planned = vp.plan_render_duration_ms(
        scenes,
        final_end_ms=timeline["final_end_ms"],
        timeline_by_id={item["id"]: item for item in timeline["entries"]},
    )
    _assert_equal(planned, manual_durations, "B2 多场景渲染时长 vs 独立手算")
    _assert_equal(len(planned), len(scenes), "B2 clip 数 == 场景数")
    _assert_equal(
        sum(planned), timeline["final_end_ms"], "B2 渲染总时长覆盖全片"
    )

    # 时间轴连续：第 i 段起点 + 第 i 段时长 == 第 i+1 段起点
    cursor = 0
    for index, duration in enumerate(planned):
        cursor += duration
        if index + 1 < len(planned):
            _assert_equal(
                cursor, scene_windows[index + 1][0], "B2 场景区间无缝隙"
            )
    print(
        f"[B2] scenes={len(scenes)} durations={planned} "
        f"final_end={timeline['final_end_ms']} warnings={len(timeline['warnings'])} "
        f"srt={srt_path.name}"
    )


# ============================================================
# B3：U4-3 render_only —— 规划语义 + 静态调用链证据
# ============================================================

def _calls_in(nodes):
    names = set()
    for node in nodes:
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                func = child.func
                if isinstance(func, ast.Name):
                    names.add(func.id)
                elif isinstance(func, ast.Attribute):
                    names.add(func.attr)
    return names


def test_b3_u4_3_render_only_semantics():
    """render_only 的契约：不进门禁、不产生 TTS/timed SRT，按 SRT 末尾渲染。

    证据分两层：
    1）规划层：plan_render_duration_ms 在无 final_timeline 时以 SRT 末尾为界，
       单场景 == SRT 末尾，多场景总时长 == SRT 末尾（独立手算）；
    2）静态层：AST 证明 _run_pipeline 中 render_only 分支不含
       generate_all_tts / prepare_final_timeline / build_audio_track 调用，
       而 `if not render_only` 分支才包含 TTS 与时间轴门禁。
    端到端执行需渲染器与媒体工具，本阶段禁止，标记 UNVERIFIED。
    """
    case_dir = _case_dir("b3")
    windows = [(0, 2500), (2500, 5000), (5000, 7500), (7500, 10000)]
    _srt_path, scenes = _build_scenes(case_dir, windows)
    srt_end_ms = 10000

    single = vp.plan_render_duration_ms([scenes[0]], final_end_ms=srt_end_ms)
    _assert_equal(single, [srt_end_ms], "B3 单场景 render_only 时长 == SRT 末尾")

    planned = vp.plan_render_duration_ms(scenes, final_end_ms=srt_end_ms)
    _assert_equal(len(planned), len(scenes), "B3 多场景 render_only clip 数")
    _assert_equal(sum(planned), srt_end_ms, "B3 多场景 render_only 总时长 == SRT 末尾")
    _assert_equal(min(planned) >= 1000, True, "B3 单段不短于 1000ms 下限")

    source = Path(vp.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    run_pipeline = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_run_pipeline"
    )
    forbidden = {"generate_all_tts", "prepare_final_timeline", "build_audio_track"}
    render_only_branches = []
    not_render_only_branches = []
    for node in ast.walk(run_pipeline):
        if not isinstance(node, ast.If):
            continue
        test_src = (ast.get_source_segment(source, node.test) or "").strip()
        if test_src == "render_only":
            render_only_branches.append(node)
        elif test_src == "not render_only":
            not_render_only_branches.append(node)

    if len(render_only_branches) < 2:
        raise AssertionError(
            f"render_only 分支数量异常：{len(render_only_branches)}（预期 >=2）"
        )
    if len(not_render_only_branches) < 1:
        raise AssertionError("未找到 `if not render_only` 分支")
    planning_branches = 0
    for node in render_only_branches:
        names = _calls_in(node.body)
        hits = names & forbidden
        if hits:
            raise AssertionError(f"render_only 分支出现被禁调用：{sorted(hits)}")
        if "plan_render_duration_ms" in names:
            planning_branches += 1
    if planning_branches < 1:
        raise AssertionError("没有任何 render_only 分支按 SRT 推导渲染时长")
    for node in not_render_only_branches:
        names = _calls_in(node.body)
        for required in ("generate_all_tts", "prepare_final_timeline"):
            if required not in names:
                raise AssertionError(
                    f"`if not render_only` 分支缺少 {required}，调用链与预期不符"
                )
    print(
        f"[B3] single={single} multiscene={planned} "
        f"render_only_branches={len(render_only_branches)} "
        f"planning_branches={planning_branches} "
        f"not_render_only_branches={len(not_render_only_branches)}"
    )


# ============================================================
# B4：默认 gap_ms=80 下 blocking 可达性（穷举实测，非常量推断）
# ============================================================

def _sweep_blocking(gap_ms):
    hits = []
    starts = (0, 100, 1000)
    windows = (100, 500, 1000)
    durations = (100, 500, 1000, 3000)
    second_starts = (0, 200, 1000, 2000)
    total = 0
    for start in starts:
        for window in windows:
            for duration in durations:
                for second_start in second_starts:
                    total += 1
                    entries = [
                        {
                            "id": 1,
                            "planned_start_ms": start,
                            "planned_end_ms": start + window,
                            "duration_ms": duration,
                            "actual_end_ms": start + duration,
                        },
                        {
                            "id": 2,
                            "planned_start_ms": second_start,
                            "planned_end_ms": second_start + window,
                            "duration_ms": duration,
                            "actual_end_ms": second_start + duration,
                        },
                    ]
                    timeline = vp.build_final_timeline(entries, gap_ms=gap_ms)
                    if timeline["has_blocking_conflicts"]:
                        hits.append((entries, timeline["blocking_conflicts"]))
    return total, hits


def test_b4_blocking_reachability_default_gap():
    """默认 gap=80：穷举 144 组双 cue 输入，blocking 必须恒不可达。

    同时证明 gap=0 时可达（否则「gap=80 不可达」无法与「用例构造能力不足」
    区分），并给出 gap=-1 的入参拒绝证据。
    """
    total80, hits80 = _sweep_blocking(GAP_MS)
    _assert_equal(total80, 144, "B4 gap=80 穷举规模")
    if hits80:
        first = hits80[0]
        raise AssertionError(
            f"默认 gap=80 下出现 {len(hits80)} 组 blocking，与契约声明不符：{first}"
        )

    total0, hits0 = _sweep_blocking(0)
    _assert_equal(total0, total80, "B4 gap=0 穷举规模一致")
    if not hits0:
        raise AssertionError(
            "gap=0 穷举未构造出 blocking，说明扫描不具备检测能力，结论不可信"
        )

    # 最小可达构造（独立手算）：gap=0 时后一条并入前一条结束点，字幕被挤压 1ms。
    entries = [
        {"id": 1, "planned_start_ms": 0, "planned_end_ms": 2500,
         "duration_ms": 2000, "actual_end_ms": 2000},
        {"id": 2, "planned_start_ms": 1000, "planned_end_ms": 3000,
         "duration_ms": 2000, "actual_end_ms": 3000},
    ]
    zero = vp.build_final_timeline(entries, gap_ms=0)
    kinds = [item["kind"] for item in zero["blocking_conflicts"]]
    _assert_equal(kinds, ["subtitle_shorter_than_speech"], "B4 gap=0 最小构造 blocking 种类")
    _assert_equal(zero["blocking_conflicts"][0]["id"], 1, "B4 blocking cue 编号")

    rejected = False
    try:
        vp.build_final_timeline(entries, gap_ms=-1)
    except ValueError:
        rejected = True
    _assert_equal(rejected, True, "B4 负 gap 必须被拒绝")
    print(
        f"[B4] gap=80: {total80} 组 0 blocking | gap=0: {len(hits0)} 组可达 "
        f"| min_gap 阈值: gap=0 可达, gap=80 不可达"
    )


# ============================================================
# B5：U4-4 覆盖保护 —— 内容哈希级证据
# ============================================================

def test_b5_u4_4_overwrite_guard_content_hash():
    """覆盖保护必须拒绝执行且一个字节都不改；render_only 目标集合不同。

    旧 U4-4 只比文件名，无法证明旧产物内容未被改写。此处对目录内全部文件
    做 SHA-256 快照前后比对。
    """
    case_dir = _case_dir("b5")
    out = case_dir / "run1"
    out.mkdir()
    (out / "scene.json").write_text('{"stale": true}', encoding="utf-8")
    (out / "run1-final.mp4").write_bytes(b"stale-final-payload")
    (out / "notes.txt").write_text("unrelated", encoding="utf-8")
    (out / ".tts").mkdir()
    (out / ".tts" / "cue-01.wav").write_bytes(b"stale-audio")

    before = _snapshot(out)
    raised = None
    try:
        vp._assert_targets_do_not_exist(out)
    except RuntimeError as exc:
        raised = exc
    if raised is None:
        raise AssertionError("存在目标产物时覆盖保护未触发")
    if "拒绝覆盖" not in str(raised):
        raise AssertionError(f"拒绝原因不明确：{raised}")
    after = _snapshot(out)
    _assert_equal(after, before, "B5 拒绝后目录内容哈希快照")
    for rel in ("scene.json", "run1-final.mp4", ".tts/cue-01.wav"):
        if rel not in str(raised):
            continue

    # render_only：不产出 voice/final/timed，因此这些既有文件不应触发拒绝。
    out2 = case_dir / "run2"
    out2.mkdir()
    (out2 / "run2-final.mp4").write_bytes(b"keep")
    (out2 / "run2-voice.m4a").write_bytes(b"keep")
    (out2 / "run2-timed.srt").write_text("keep", encoding="utf-8")
    targets = vp._target_artifact_paths(out2, render_only=True)
    for unexpected in ("run2-final.mp4", "run2-voice.m4a", "run2-timed.srt"):
        if any(path.name == unexpected for path in targets):
            raise AssertionError(f"render_only 目标集合不应包含 {unexpected}")
    vp._assert_targets_do_not_exist(out2, render_only=True)

    # 非 render_only：同一目录必须判定为冲突，且内容哈希不变。
    before2 = _snapshot(out2)
    try:
        vp._assert_targets_do_not_exist(out2, render_only=False)
    except RuntimeError:
        pass
    else:
        raise AssertionError("非 render_only 目标已存在但未拒绝")
    _assert_equal(_snapshot(out2), before2, "B5 render_only 判定不修改文件")

    # 空目录：必须放行。
    out3 = case_dir / "run3"
    out3.mkdir()
    vp._assert_targets_do_not_exist(out3)
    _assert_equal(_snapshot(out3), {}, "B5 空目录不被写入")

    # 单个产物级保护：write_timed_srt 二次写同一路径必须拒绝且内容不变。
    timeline = vp.build_final_timeline([
        {"id": 1, "planned_start_ms": 0, "planned_end_ms": 2000,
         "duration_ms": 1500, "actual_end_ms": 1500},
    ])
    subtitles = [{"id": 1, "start_ms": 0, "end_ms": 2000, "text": "hello"}]
    first = vp.write_timed_srt(timeline, subtitles, case_dir / "timed" / "t.srt")
    digest = _sha256(first)
    refused = False
    try:
        vp.write_timed_srt(timeline, subtitles, first)
    except FileExistsError:
        refused = True
    _assert_equal(refused, True, "B5 timed SRT 二次写必须拒绝")
    _assert_equal(_sha256(first), digest, "B5 timed SRT 内容未被改写")
    print(
        f"[B5] refused_targets={len(before)} files_snapshot_equal=True "
        f"render_only_exempt=True timed_srt_sha={digest[:12]}"
    )


# ============================================================
# B6：测试有效性元验证（断言是否真的有检测能力）
# ============================================================

def test_b6_assertion_detection_power():
    """受控错误输入必须让判定函数报错；恢复正确输入后必须通过。

    _assert_equal / _manual_layout / _manual_conflicts 是本文件所有断言的
    核心判定器。此处用错误期望值验证其可失败，防止「断言恒真」。
    """
    _assert_equal(1, 1, "B6 正常输入")
    failed = False
    try:
        _assert_equal(1, 2, "B6 故意错误的期望")
    except AssertionError:
        failed = True
    _assert_equal(failed, True, "B6 _assert_equal 具备检测能力")

    rows = [(1, 0, 2500, 2000), (2, 1000, 3000, 2000)]
    layout = _manual_layout(rows, 0)
    conflicts = _manual_conflicts([dict(item) for item in layout], 0)
    kinds = [item["kind"] for item in conflicts]
    _assert_equal(
        kinds,
        ["subtitle_window_capped", "subtitle_shorter_than_speech"],
        "B6 手算判定器同时识别 warning 与 blocking",
    )
    _assert_equal(
        [item["severity"] for item in conflicts],
        ["warning", "blocking"],
        "B6 手算判定器严重程度分级",
    )

    # 同一窗口重叠输入在 gap=80 下只应剩 warning，不得升级为 blocking；
    # 这条断言同时证明判定器不会把「有冲突」与「阻塞」混为一谈。
    gap80_layout = _manual_layout(rows, 80)
    gap80_conflicts = _manual_conflicts([dict(item) for item in gap80_layout], 80)
    _assert_equal(
        [item["kind"] for item in gap80_conflicts],
        ["subtitle_window_capped"],
        "B6 gap=80 仅保留 warning",
    )
    _assert_equal(
        [item for item in gap80_conflicts if item["severity"] == "blocking"],
        [],
        "B6 gap=80 不产生 blocking",
    )

    # 生产侧的入参校验同样可被触发（证明用例确实打到生产逻辑，而非替身）。
    invalid_duration_rejected = False
    try:
        vp.build_final_timeline([
            {"id": 1, "planned_start_ms": 0, "planned_end_ms": 1000,
             "duration_ms": 0, "actual_end_ms": 0},
        ])
    except ValueError:
        invalid_duration_rejected = True
    _assert_equal(invalid_duration_rejected, True, "B6 生产校验拒绝 0 时长语音")
    print("[B6] detection_power=ok invalid_input_guard=ok")


# ============================================================
# B7：可重跑性 —— 无历史产物依赖，两个独立目录各自成功
# ============================================================

def test_b7_repeatable_isolated_runs():
    """同一套推导在两个全新目录重复执行，结果一致且互不干扰。"""
    case_dir = _case_dir("b7")
    results = []
    for round_index in (1, 2):
        run_dir = case_dir / f"round{round_index}"
        run_dir.mkdir()
        _srt, scenes = _build_scenes(
            run_dir, [(0, 2500), (2000, 4500)], group_target_ms=3000
        )
        rows = _expected_rows_from_scenes(scenes, [1000, 3000])
        expected = _manual_layout(rows, GAP_MS)
        timeline = vp.build_final_timeline([
            {
                "id": cue_id,
                "planned_start_ms": start,
                "planned_end_ms": end,
                "duration_ms": duration,
                "actual_end_ms": start + duration,
            }
            for cue_id, start, end, duration in rows
        ])
        subtitles = [
            {"id": sub["id"], "start_ms": sub["start_ms"],
             "end_ms": sub["end_ms"], "text": sub["text"]}
            for scene in scenes for sub in scene.subtitles
        ]
        path = vp.write_timed_srt(timeline, subtitles, run_dir / "timed.srt")
        results.append((
            len(scenes),
            timeline["final_end_ms"],
            expected[-1]["final_end_ms"],
            _sha256(path),
        ))
    _assert_equal(results[0][:3], results[1][:3], "B7 两次运行推导结果一致")
    if results[0][3] != results[1][3]:
        raise AssertionError("B7 相同输入应生成相同 timed SRT 内容")
    _assert_equal(
        sorted(p.name for p in case_dir.iterdir()), ["round1", "round2"], "B7 目录隔离"
    )
    print(f"[B7] rounds=2 stable_hash={results[0][3][:12]} work_root={WORK_ROOT}")


# ============================================================
# runner
# ============================================================

CASES = [
    ("B1/U3-1", test_b1_u3_1_three_way_cross_check),
    ("B2/U4-2", test_b2_u4_2_multiscene_success_path),
    ("B3/U4-3", test_b3_u4_3_render_only_semantics),
    ("B4/U3-3", test_b4_blocking_reachability_default_gap),
    ("B5/U4-4", test_b5_u4_4_overwrite_guard_content_hash),
    ("B6/META", test_b6_assertion_detection_power),
    ("B7/RERUN", test_b7_repeatable_isolated_runs),
]


def main():
    print(f"[work] M53B_TMP={WORK_ROOT}")
    results = []
    for case_id, func in CASES:
        started = time.time()
        try:
            func()
            results.append((case_id, "PASS", ""))
        except Exception as exc:  # noqa: BLE001
            results.append((case_id, "FAIL", f"{type(exc).__name__}: {exc}"))
            traceback.print_exc()
        print(f"[{results[-1][1]}] {case_id} ({time.time() - started:.2f}s)")

    print("\n=== summary ===")
    for case_id, status, detail in results:
        print(f"{case_id}\t{status}\t{detail}")
    failed = [item for item in results if item[1] == "FAIL"]
    print(
        f"\nPASS={sum(1 for item in results if item[1] == 'PASS')} "
        f"FAIL={len(failed)} SKIP=0"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
