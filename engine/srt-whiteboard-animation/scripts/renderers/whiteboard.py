"""Whiteboard Renderer，调用现有白板渲染实现。

M4-1 时长契约：白板渲染必须消费视频流水线计算出的有效场景时长
（video_pipeline.plan_render_duration_ms 的结果，已写入 Scene.duration_ms），
而不是继续沿用 annotation 里可能过期的 sceneDurationMs。

启用方式为显式开启（fit_scene_duration=True），默认保持旧行为不变：
- 旧调用方（不传 total_ms、不开启 fit_scene_duration）完全不受影响；
- 显式传 total_ms 的旧调用方保持原行为（仅透传 --total-ms）。

底层 render_stream_whiteboard 的时长规则（本文件不修改它，只在调用侧适配）：
- 输出时长 = max(total_ms, 书写动画结束时刻 + 500ms 凝视尾巴)；
  因此仅当 annotation 的书写编排结束时点 ≤ total_ms - 500 时，成片时长才等于 total_ms。

M4-4 时间编排根因修复：
- 底层是按元素「串行累加」写帧的（cur_ms += ink_frames + color_frames），
  真正决定成片长度的是每元素帧数之和 Σframes，而不是时间轴末端 max(start+duration)。
  当编排存在重叠（durationMs 大于相邻 start 间隔）时，Σframes 远大于 max_end，
  仅按 max_end 缩放会把成片做到目标时长的 1.2~1.5 倍，只能靠后处理变速压缩。
- 这里用与底层一致的帧预算模型（权重 2:1、max(1, round) 下限）计算可用帧数，
  在必要时按帧重新分配每个元素的书写时长并顺序铺排，使渲染器直接落到目标时长。
- 预算不足（元素过多、目标过短）时不删除任何笔画，改为输出可诊断警告，
  超出的部分仍由 video_pipeline.fit_clip_duration() 兜底。
"""

import hashlib
import importlib.util
import json
from pathlib import Path

from .base import Renderer, SceneLike
from scene.normalizer import normalize_scene

# 定位 scripts/ 目录，通过 importlib 加载 render_stream_whiteboard 模块
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _SCRIPTS_DIR.parent

_spec = importlib.util.spec_from_file_location(
    "render_stream_whiteboard",
    _SCRIPTS_DIR / "render_stream_whiteboard.py",
)
_rsw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_rsw)

# 底层渲染器在 --total-ms 之后至少停留 0.5s 完整原图
# （render_stream_whiteboard.RegionStreamRenderer.render_to: gaze_until = max(total_ms, cur_ms + 500)）。
# 欲让成片时长严格等于目标时长，annotation 的书写编排必须为这段尾巴预留时间。
GAZE_TAIL_MS = 500

# M4-4：中间产物（重新编排后的 annotation）统一收进输出目录下的子目录，
# 避免 .fit*.annotation.json 散落在最终产物旁边；不覆盖源 annotation。
TIMING_SUBDIR = ".timing"


class _TimingModel:
    """复刻 render_stream_whiteboard.RegionStreamRenderer.render_to 的帧预算模型。

    只做只读复刻，不修改底层实现：
        ink_frames   = max(1, round(dur * ink_weight / (ink+color) * fps / 1000))
        color_frames = max(1, round(dur * color_weight / (ink+color) * fps / 1000))
        cur_ms      += (ink_frames + color_frames) * 1000 / fps
        fill_static(start): n = round((start - cur_ms) / ms_per_frame)
    """

    def __init__(self, fps: int, ink_weight: int = 2, color_weight: int = 1):
        self.fps = max(1, int(fps))
        self.ms_per_frame = 1000.0 / self.fps
        self.ink_weight = int(ink_weight)
        self.color_weight = int(color_weight)
        self.weight_sum = max(1, self.ink_weight + self.color_weight)

    def ink_frames(self, dur_ms: float) -> int:
        return max(
            1,
            round(dur_ms * self.ink_weight / self.weight_sum * self.fps / 1000),
        )

    def color_frames(self, dur_ms: float) -> int:
        return max(
            1,
            round(dur_ms * self.color_weight / self.weight_sum * self.fps / 1000),
        )

    def frames(self, dur_ms: float) -> int:
        return self.ink_frames(dur_ms) + self.color_frames(dur_ms)

    def start_frames(self, start_ms: float) -> int:
        return int(round(float(start_ms) / self.ms_per_frame))

    def simulate(self, plan: list[tuple[float, float]]) -> int:
        """按底层串行规则模拟写出的总帧数。"""
        cur = 0
        for start_ms, dur_ms in plan:
            gap = self.start_frames(start_ms) - cur
            if gap > 0:
                cur += gap
            cur += self.frames(dur_ms)
        return cur

    def fit_duration(self, want_frames: int) -> int:
        """求最大的 durationMs，使 frames(durationMs) <= want_frames。"""
        want_frames = max(2, int(want_frames))
        lo, hi = 1, max(2, int(want_frames * self.ms_per_frame * 1.5) + 5)
        best = 1
        while lo <= hi:
            mid = (lo + hi) // 2
            if self.frames(mid) <= want_frames:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        return best


def _allocate_frames(weights: list[float], budget_frames: int, min_frames: int = 2) -> list[int]:
    """把帧预算按权重分给每个元素，保证每个元素至少 min_frames 帧。"""
    count = len(weights)
    base = min_frames * count
    if budget_frames <= base:
        return [min_frames] * count

    remain = budget_frames - base
    safe_weights = [max(1.0, float(weight)) for weight in weights]
    total_weight = sum(safe_weights)
    alloc = [0] * count
    remainders: list[tuple[float, int]] = []
    for index, weight in enumerate(safe_weights):
        exact = remain * weight / total_weight
        whole = int(exact)
        alloc[index] = whole
        remainders.append((exact - whole, index))
    leftover = remain - sum(alloc)
    remainders.sort(key=lambda item: (-item[0], item[1]))
    for _, index in remainders[: max(0, leftover)]:
        alloc[index] += 1
    return [min_frames + extra for extra in alloc]


class WhiteboardRenderer(Renderer):
    """白板动画 Renderer，调用现有白板渲染实现。

    通过 importlib 加载 render_stream_whiteboard.py 并调用其 main(argv)，
    避免复制白板渲染代码。
    """

    def __init__(
        self,
        hand: str | None = None,
        ink_path: str = "grid",
        color_fill: str = "contour-wipe",
        pause: str = "heavy",
        fps: int | None = None,
        grid_edge: int | None = None,
        brush_radius: int | None = None,
        cap_long_edge: int | None = None,
        total_ms: int | None = None,
        bare_tip: bool = False,
        fit_scene_duration: bool = False,
    ):
        self.hand = hand
        self.ink_path = ink_path
        self.color_fill = color_fill
        self.pause = pause
        self.fps = fps
        self.grid_edge = grid_edge
        self.brush_radius = brush_radius
        self.cap_long_edge = cap_long_edge
        self.total_ms = total_ms
        self.bare_tip = bare_tip
        # 可选：让白板渲染采用 Scene.duration_ms（流水线计算出的有效场景时长），
        # 并据此校正 annotation 的书写编排。默认关闭以保持旧调用行为。
        self.fit_scene_duration = fit_scene_duration
        # M4-4：最近一次渲染的时间编排诊断，供流水线写入 manifest。
        self.last_timing_plan: dict | None = None
        self.last_fit_path: Path | None = None

    def capabilities(self) -> frozenset[str]:
        return frozenset({"story", "illustration", "hand_drawn", "historical", "character", "scene"})

    def resolve_target_ms(self, scene: SceneLike) -> int | None:
        """本次渲染实际采用的目标时长（毫秒）；None 表示沿用底层 annotation 行为。"""
        if self.total_ms is not None:
            return int(self.total_ms)
        if not self.fit_scene_duration:
            return None
        duration_ms = int(getattr(scene, "duration_ms", 0) or 0)
        return duration_ms if duration_ms > 0 else None

    def render(self, scene: SceneLike, output_path: Path) -> Path:
        # 兼容 legacy dict，统一转换为 Scene
        scene = normalize_scene(scene) if isinstance(scene, dict) else scene
        # 从 scene.visual 中获取 image 和 annotation 路径
        image = Path(scene.visual["image"])
        annotation = Path(scene.visual["annotation"])
        hand = self.hand or str(_SCRIPTS_DIR.parent / "assets" / "drawing-hand.png")
        self.last_timing_plan = None
        self.last_fit_path = None

        target_ms = self.resolve_target_ms(scene)
        # 仅在「场景时长驱动」模式下校正旧 annotation，显式 total_ms 的旧路径保持原样
        if target_ms is not None and self.total_ms is None:
            annotation = self._fit_annotation(annotation, target_ms, Path(output_path))

        # 构造 render_stream_whiteboard.main(argv) 参数
        argv = [str(image), str(annotation), str(output_path), hand]

        if target_ms is not None:
            argv.extend(["--total-ms", str(target_ms)])
        if self.bare_tip:
            argv.append("--bare-tip")
        if self.ink_path != "grid":
            argv.extend(["--ink-path", self.ink_path])
        if self.color_fill != "contour-wipe":
            argv.extend(["--color-fill", self.color_fill])
        if self.pause != "heavy":
            argv.extend(["--pause", self.pause])
        if self.fps is not None:
            argv.extend(["--fps", str(self.fps)])
        if self.grid_edge is not None:
            argv.extend(["--grid-edge", str(self.grid_edge)])
        if self.brush_radius is not None:
            argv.extend(["--brush-radius", str(self.brush_radius)])
        if self.cap_long_edge is not None:
            argv.extend(["--cap-long-edge", str(self.cap_long_edge)])

        _rsw.main(argv)

        return output_path

    # ------------------------------------------------------------------
    # M4-4：按帧预算重新编排 annotation（不覆盖源文件）
    # ------------------------------------------------------------------

    def _timing_model(self) -> _TimingModel:
        cfg = _rsw.sr.Config()
        fps = int(self.fps if self.fps is not None else getattr(cfg, "fps", 60))
        return _TimingModel(
            fps=fps,
            ink_weight=getattr(cfg, "ink_weight", 2),
            color_weight=getattr(cfg, "color_weight", 1),
        )

    def _fit_annotation(self, annotation: Path, target_ms: int, output_path: Path) -> Path:
        """把 annotation 的书写编排校正到目标时长的帧预算内。

        返回可直接交给底层渲染器的 annotation 路径：
        - 编排本身可在预算内完成 → 仅同步 sceneDurationMs，写出旁挂文件（不改节奏）；
        - 编排超出预算 → 按底层帧模型重新分配每元素时长并顺序铺排，写出旁挂文件；
        - 预算不足以每元素至少 2 帧 → 保留全部元素，输出可诊断警告（交给后处理兜底）。
        任何情况下都不覆盖、不删除源 annotation。
        """
        try:
            source_bytes = annotation.read_bytes()
            data = json.loads(source_bytes.decode("utf-8"))
        except (OSError, ValueError):
            # annotation 无法解析时不擅自处理，交由底层渲染器按原逻辑报错/兜底
            return annotation
        if not isinstance(data, dict) or not isinstance(data.get("elements"), list):
            return annotation

        elements = data["elements"]
        reveals = []
        for element in elements:
            reveal = element.get("reveal") if isinstance(element, dict) else None
            if not isinstance(reveal, dict):
                # 结构不符合预期时不擅自重排，交由底层处理
                return annotation
            try:
                start = float(reveal.get("startMs", 0) or 0)
                duration = float(reveal.get("durationMs", 0) or 0)
            except (TypeError, ValueError):
                return annotation
            reveals.append((start, duration))
        if not reveals:
            return annotation

        model = self._timing_model()
        budget_frames = max(0, int(round(target_ms / model.ms_per_frame)) - int(round(GAZE_TAIL_MS / model.ms_per_frame)))
        ordered = sorted(reveals, key=lambda item: item[0])
        original_plan = model.simulate(ordered)
        original_end_ms = max(start + duration for start, duration in ordered)
        declared = data.get("sceneDurationMs")
        warnings: list[str] = []
        budget_insufficient = False

        if original_plan <= budget_frames and original_end_ms <= target_ms - GAZE_TAIL_MS:
            # 编排本身放得下：不动节奏，只同步声明的场景时长
            plan_durations = [duration for _, duration in ordered]
            plan_starts = [start for start, _ in ordered]
            mode = "aligned"
            allocated = original_plan
        else:
            min_frames_total = 2 * len(ordered)
            weights = [max(1.0, duration) for _, duration in ordered]
            if budget_frames < min_frames_total:
                budget_insufficient = True
                frame_alloc = [2] * len(ordered)
                warnings.append(
                    f"budget_insufficient: target={target_ms}ms budget_frames={budget_frames} "
                    f"elements={len(ordered)} min_frames={min_frames_total}"
                )
            else:
                frame_alloc = _allocate_frames(weights, budget_frames)
            plan_starts = []
            plan_durations = []
            cursor = 0
            for want_frames in frame_alloc:
                duration = model.fit_duration(want_frames)
                # 把标称 durationMs 对齐到该元素实际占用的整帧时长，
                # 避免 startMs + durationMs 因标称值偏大而虚超预算。
                settled = model.frames(duration) * model.ms_per_frame
                if settled >= 1 and model.frames(settled) <= want_frames:
                    duration = settled
                # 用帧数换算毫秒起点，保证 fill_static 不产生额外补帧
                plan_starts.append(round(cursor * model.ms_per_frame, 3))
                plan_durations.append(duration)
                cursor += model.frames(duration)
            allocated = cursor
            mode = "refit"
            # 收紧检查：极端取整导致超预算时，逐个削减最长元素
            while allocated > budget_frames:
                index = max(range(len(plan_durations)), key=lambda i: plan_durations[i])
                if plan_durations[index] <= 1:
                    budget_insufficient = True
                    warnings.append("budget_insufficient: cannot shrink further without dropping strokes")
                    break
                plan_durations[index] -= 1
                allocated = model.simulate(list(zip(plan_starts, plan_durations)))

        ordered_elements = [
            element
            for element, _ in sorted(
                zip(elements, reveals),
                key=lambda pair: pair[1][0],
            )
        ]
        rewritten: list[dict] = []
        for element, start, duration in zip(ordered_elements, plan_starts, plan_durations):
            reveal = dict(element["reveal"])
            reveal["startMs"] = start if isinstance(start, float) and start != int(start) else int(start)
            reveal["durationMs"] = max(1, int(duration))
            new_element = dict(element)
            new_element["reveal"] = reveal
            rewritten.append(new_element)

        data["elements"] = rewritten
        data["sceneDurationMs"] = int(target_ms)

        plan_end_ms = 0.0
        for start, duration in zip(plan_starts, plan_durations):
            plan_end_ms = max(plan_end_ms, float(start) + float(duration))
        # 编排结束时刻同时受帧预算约束（底层按帧累加写帧）
        plan_end_ms = min(plan_end_ms, allocated * model.ms_per_frame)

        self.last_timing_plan = {
            "mode": mode,
            "target_ms": int(target_ms),
            "fps": model.fps,
            "budget_ms": int(round(budget_frames * model.ms_per_frame)),
            "budget_frames": int(budget_frames),
            "allocated_frames": int(allocated),
            "planned_ms": int(round(allocated * model.ms_per_frame)),
            "plan_end_ms": int(round(plan_end_ms)),
            "elements": len(ordered),
            "source_declared_ms": declared if isinstance(declared, (int, float)) else None,
            "source_planned_ms": int(round(original_plan * model.ms_per_frame)),
            "budget_insufficient": budget_insufficient,
            "warnings": warnings,
        }
        if budget_insufficient:
            print(
                "[warn][whiteboard] "
                f"时间预算不足以按当前笔画数完成书写：target={target_ms}ms "
                f"elements={len(ordered)} planned={self.last_timing_plan['planned_ms']}ms "
                "（保留全部笔画，超时部分由 fit_clip_duration 兜底）"
            )
        if warnings:
            for warning in warnings:
                print(f"[warn][whiteboard] {warning}")

        fitted = self._timing_output_path(output_path, int(target_ms), source_bytes, model.fps)
        try:
            fitted.parent.mkdir(parents=True, exist_ok=True)
            if not fitted.exists():
                fitted.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            print(f"[warn][whiteboard] 无法写入时间编排中间产物: {exc}；回退为原始 annotation")
            self.last_timing_plan["warnings"].append(f"timing_artifact_write_failed: {exc}")
            self.last_timing_plan["mode"] = "fallback"
            self.last_fit_path = None
            return annotation
        self.last_fit_path = fitted
        return fitted

    @staticmethod
    def _timing_output_path(
        output_path: Path,
        target_ms: int,
        source_bytes: bytes,
        fps: int,
    ) -> Path:
        """中间产物路径：输出目录下 .timing/，文件名含源内容指纹，避免复用过期编排。"""
        digest = hashlib.sha1(
            source_bytes + f"|{target_ms}|{fps}".encode("utf-8")
        ).hexdigest()[:8]
        annotation_stem = Path(output_path).stem
        return (
            Path(output_path).parent
            / TIMING_SUBDIR
            / f"{annotation_stem}.fit{target_ms}.{digest}.annotation.json"
        )
