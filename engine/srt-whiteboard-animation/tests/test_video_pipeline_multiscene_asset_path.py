"""Phase 1A regression test: whiteboard multi-scene local-asset path.

Purpose:
    Drive video_pipeline.main() with a no-manual-image, multi-scene input whose
    scenes resolve to LOCAL whiteboard assets, and prove that every planned scene
    is actually handed to the Whiteboard Renderer with a usable annotation.

    This closes the assertion gap left by tests/test_scene_loss_fix.py, which only
    checks scene.json and the merged render.mp4 and never verifies that the
    renderer was called once per scene.

Defects this test is written to expose (all observed at the time of writing):
    1. Silent scene skip: the per-scene local-asset branch routes to
       WhiteboardRenderer.render(), which reads scene.visual["annotation"] and
       raises KeyError because the unified path never runs the annotation bridge.
       Scenes after the first can vanish without any assertion catching it.
    2. Missing annotation: no planned scene receives its annotation preparation.
    3. Incomplete merge input: merge_scenes_main receives fewer clips than
       planned scenes.

The three defects are reported in separate, mutually exclusive branches so a
failure always says which one occurred.

Checks (no network, no LLM, no TTS, no real video render):
    - TTS, audio merge, final subtitle pass and merge are stubbed at the
      video_pipeline module level, matching the convention of
      tests/test_scene_loss_fix.py and tests/test_cinematic_renderer.py.
    - The renderer factory is wrapped so each renderer.render() call is recorded;
      the stub writes a small placeholder file instead of encoding video.
    - Annotation generation stays REAL: each planned scene is resolved through
      auto_annotate_bridge + bridge_whiteboard + WhiteboardRenderer.render(), so
      the test asserts on the actual annotation JSON files on disk.

Input data:
    - A 3-cue SRT generated in an isolated temp dir, each cue naming a distinct
      subject so each scene resolves to a distinct local image asset.
    - Local PNG assets generated with PIL (16x16) in the same temp dir.

Output data:
    - Nothing persistent. All state lives in a tempfile.mkdtemp() directory,
      removed at the end. No output/phase... path is touched.

Usage:
    PYTHONPATH=scripts .venv/bin/python \
        tests/test_video_pipeline_multiscene_asset_path.py
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
import traceback
from pathlib import Path
from unittest import mock

from PIL import Image

from parse_srt import parse_srt
from scene.planner import plan_srt_grouped
from scene.visual_representation import resolve_visual_representation
from scene.whiteboard_bridge import bridge_whiteboard
import video_pipeline

SCRIPT_START = time.time()

SAMPLE_SRT = """1
00:00:00,000 --> 00:00:04,500
长城的故事开始了，远处的山峰连绵起伏。

2
00:00:04,500 --> 00:00:09,000
富士山脚下，森林里的场景继续展开。

3
00:00:09,000 --> 00:00:13,500
山之间的铁路延伸开，整段旅程结束。
"""

# subject slug -> (base name, gradient orientation, base RGB)
ASSETS = [
    ("wall", "horizontal", (90, 40, 40)),
    ("mountain", "vertical", (30, 60, 110)),
    ("mountains", "vertical", (110, 60, 40)),
]

# group_target_ms equals one cue span so the greedy grouper keeps one cue per
# scene. The 12000ms planner cap would otherwise merge all three cues (total
# 13.5s) into one or two scenes, defeating the multi-scene check.
GROUP_TARGET_MS = 4500


class PipelineOutcome:
    """Collected observations of one main() invocation."""

    def __init__(self) -> None:
        self.scene_json = None
        self.render_calls: list[dict] = []
        self.merge_inputs: list[str] | None = None
        self.render_mp4: Path | None = None
        self.tmp_root: Path | None = None
        self.exception: BaseException | None = None
        self.traceback: str | None = None


def _make_asset_images(asset_dir: Path) -> list[Path]:
    """Generate distinct local PNG assets in an isolated directory."""
    asset_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for slug, direction, (r, g, b) in ASSETS:
        img = Image.new("RGB", (16, 16))
        px = img.load()
        for x in range(16):
            for y in range(16):
                t = (x if direction == "horizontal" else y) / 15.0
                px[x, y] = (
                    int(r * (1 - t) + 255 * t),
                    int(g * (1 - t) + 255 * t),
                    int(b * (1 - t) + 255 * t),
                )
        out = asset_dir / f"{slug}.png"
        img.save(out)
        paths.append(out)
    return paths


def _fake_generate_all_tts(provider, subtitles, tts_dir):
    tts_dir.mkdir(parents=True, exist_ok=True)
    fake_files = []
    for subtitle in subtitles:
        mp3 = tts_dir / f"{subtitle['id']:03d}.mp3"
        mp3.write_bytes(b"FAKE")
        fake_files.append({
            "id": subtitle["id"],
            "start": subtitle["start"],
            "end": subtitle["end"],
            "text": subtitle["text"],
            "file": mp3,
        })
    return fake_files


def _build_renderer_stub(
    real_create_renderer,
    renderer_calls: list[dict],
    assets_dir: Path,
):
    """Record every render() call and check the annotation the pipeline handed over."""

    def recording_create_renderer(name, **kwargs):
        renderer = real_create_renderer(name, **kwargs)

        def fake_render(scene, output_path):
            record = {
                "renderer": name,
                "output_path": Path(output_path),
                "scene_id": getattr(scene, "id", None),
                "visual": dict(getattr(scene, "visual", {}) or {}),
                "duration_ms": getattr(scene, "duration_ms", None),
                "error": None,
            }
            try:
                annotation = scene.visual["annotation"]
                annotation_path = Path(annotation)
                record["annotation_exists"] = annotation_path.exists()
                record["annotation_dir_ok"] = str(annotation_path.parent) == str(assets_dir)
            except KeyError as exc:
                record["error"] = f"KeyError: {exc}"
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"FAKE-CLIP")
            renderer_calls.append(record)
            return output_path

        return mock.Mock(
            wraps=renderer,
            render=fake_render,
            last_timing_plan=None,
            last_fit_path=None,
        )

    return recording_create_renderer


def probe_local_asset_representation(tmp_root: Path) -> list[dict]:
    """Precondition probe: do the planned scenes actually resolve to local assets?

    This pins the test onto the local_asset branch instead of a procedural-svg
    fallback, and records whether that branch supplies an annotation.
    """
    asset_dir = tmp_root / "assets"
    srt_path = tmp_root / "sample.srt"
    _make_asset_images(asset_dir)
    srt_path.write_text(SAMPLE_SRT, encoding="utf-8")

    cues = parse_srt(srt_path.read_text(encoding="utf-8"))
    scenes = plan_srt_grouped(
        cues,
        renderer="whiteboard",
        target_duration_ms=GROUP_TARGET_MS,
        min_duration_ms=4000,
        max_duration_ms=12000,
    )

    records: list[dict] = []
    for scene in scenes:
        representation = resolve_visual_representation(
            scene,
            asset_roots=[asset_dir],
            renderer_preference="whiteboard",
        )
        payload = dict(representation.payload)
        record = {
            "scene_id": scene.id,
            "kind": representation.kind,
            "renderer": representation.renderer,
            "payload": payload,
            "has_annotation": "annotation" in payload,
            "bridge_raises": None,
        }
        try:
            bridge_whiteboard(scene)
        except Exception as exc:  # noqa: BLE001 - recorded, not raised
            record["bridge_raises"] = f"{type(exc).__name__}: {exc}"
        records.append(record)

    assert len(scenes) >= 2, (
        f"测试前置条件不成立: 计划场景数少于 2 ({len(scenes)})，"
        f"无法检验多场景路径。调整 SAMPLE_SRT / GROUP_TARGET_MS 后重试。"
    )
    assert all(item["kind"] == "local_asset" for item in records), (
        f"测试前置条件不成立: 并非所有场景都命中本地素材分支，"
        f"实际 kinds={[item['kind'] for item in records]}。"
        f"检查 ASSETS 中的文件名是否与视觉层解析出的 subject 一致。"
    )
    return records



def run_pipeline_capture() -> PipelineOutcome:
    """Run main() once with all side effects stubbed; never raises."""
    result = PipelineOutcome()
    result.render_mp4 = Path(tempfile.mkdtemp()) / "render.mp4"

    try:
        tmp_root = Path(tempfile.mkdtemp(prefix="wb-multiscene-"))
        assets_dir = tmp_root / "assets"
        srt_path = tmp_root / "sample.srt"
        out_dir = tmp_root / "out"

        _make_asset_images(assets_dir)
        srt_path.write_text(SAMPLE_SRT, encoding="utf-8")

        renderer_calls: list[dict] = []

        def fake_create_tts_provider(name, **kwargs):
            return mock.Mock(synthesize=lambda text, output: None)

        def fake_build_audio_track(
            audio_files,
            output_audio,
            timeline_report=None,
            final_timeline=None,
        ):
            Path(output_audio).write_bytes(b"FAKE-AUDIO")

        def fake_build_final_video(video, audio, srt, output, force_style=None):
            Path(output).write_bytes(b"FAKE-FINAL")

        real_ffprobe_duration_ms = video_pipeline.ffprobe_duration_ms

        def fake_ffprobe_duration_ms(path):
            # Fake TTS files contain placeholders, so supply a deterministic
            # duration to the real final-timeline planner.
            if Path(path).parent.name == ".tts":
                return 0.5
            return real_ffprobe_duration_ms(path)

        real_create_renderer = video_pipeline.create_renderer

        def fake_merge_scenes_main(argv):
            argv = [str(item) for item in argv]
            if "--inputs" in argv:
                cursor = argv.index("--inputs") + 1
                inputs = []
                while cursor < len(argv) and argv[cursor] != "--output":
                    inputs.append(argv[cursor])
                    cursor += 1
                result.merge_inputs = inputs
                # Mirror real merge_scenes_main: it writes a merged render.mp4
                # at the --output path. Without this, the D-class assertion on
                # result.render_mp4.exists() fails even when the pipeline is
                # correctly wired.
                if cursor < len(argv) - 1:
                    out_path = Path(argv[cursor + 1])
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_bytes(b"FAKE-MERGED-RENDER")
            else:
                result.merge_inputs = [item for item in argv if item.endswith(".mp4")]

        originals = (
            video_pipeline.create_tts_provider,
            video_pipeline.generate_all_tts,
            video_pipeline.build_audio_track,
            video_pipeline.build_final_video,
            video_pipeline.ffprobe_duration_ms,
            video_pipeline.create_renderer,
            video_pipeline.merge_scenes_main,
        )
        video_pipeline.create_tts_provider = fake_create_tts_provider
        video_pipeline.generate_all_tts = _fake_generate_all_tts
        video_pipeline.build_audio_track = fake_build_audio_track
        video_pipeline.build_final_video = fake_build_final_video
        video_pipeline.ffprobe_duration_ms = fake_ffprobe_duration_ms
        video_pipeline.create_renderer = _build_renderer_stub(
            real_create_renderer, renderer_calls, assets_dir
        )
        video_pipeline.merge_scenes_main = fake_merge_scenes_main

        try:
            video_pipeline.main(
                str(srt_path),
                str(out_dir),
                renderer_name="whiteboard",
                tts_name="edge",
                asset_roots=[str(assets_dir)],
                group_target_ms=GROUP_TARGET_MS,
            )
            result.scene_json = json.loads(
                (out_dir / "scene.json").read_text(encoding="utf-8")
            )
            result.render_calls = renderer_calls
            result.render_mp4 = out_dir / f"{out_dir.name}-render.mp4"
        except BaseException as exc:  # noqa: BLE001 - reported below, never swallowed
            result.exception = exc
            result.render_calls = renderer_calls
            result.traceback = traceback.format_exc()
        finally:
            (
                video_pipeline.create_tts_provider,
                video_pipeline.generate_all_tts,
                video_pipeline.build_audio_track,
                video_pipeline.build_final_video,
                video_pipeline.ffprobe_duration_ms,
                video_pipeline.create_renderer,
                video_pipeline.merge_scenes_main,
            ) = originals
    except BaseException as exc:  # noqa: BLE001
        result.exception = exc
        result.traceback = traceback.format_exc()
    finally:
        result.tmp_root = tmp_root
    return result


def test_whiteboard_multiscene_local_asset_path() -> None:
    result = run_pipeline_capture()
    try:
        calls = result.render_calls
        scene_json = result.scene_json or []
        failures: list[str] = []

        probe = probe_local_asset_representation(Path(tempfile.mkdtemp(prefix="wb-multiscene-probe-")))
        for item in probe:
            print(
                f"[info]   probe scene={item['scene_id']} kind={item['kind']} "
                f"has_annotation={item['has_annotation']} "
                f"bridge_raises={item['bridge_raises']} payload_keys={sorted(item['payload'])}"
            )

        print(f"[info] renderer.render() calls        = {len(calls)}")
        print(f"[info] scenes in scene.json           = {len(scene_json)}")
        print(f"[info] merge inputs captured          = {len(result.merge_inputs or [])}")
        if result.exception is not None:
            print(f"[info] pipeline exception             = {type(result.exception).__name__}: {result.exception}")
        for record in calls:
            print(
                f"[info]   call scene={record['scene_id']} clip={record['output_path'].name} "
                f"duration_ms={record['duration_ms']} "
                f"annotation_exists={record.get('annotation_exists')} error={record.get('error')}"
            )

        # --- failure category A: a planned scene was silently skipped -------------
        if result.exception is not None:
            dropped = len(scene_json) - len(calls)
            if isinstance(result.exception, KeyError) and dropped > 0:
                failures.append(
                    f"静默丢场景 (silent scene skip): pipeline raised "
                    f"{type(result.exception).__name__}: {result.exception} after only "
                    f"{len(calls)}/{len(scene_json)} scenes were rendered. The "
                    f"unified local-asset branch routes to WhiteboardRenderer.render(), "
                    f"which requires visual.annotation that the branch never prepares."
                )
                if result.traceback:
                    failures.append("pipeline traceback (truncated):\n" + result.traceback[-1500:])
            else:
                failures.append(
                    f"pipeline raised unexpectedly: {type(result.exception).__name__}: {result.exception}"
                )
                if result.traceback:
                    failures.append("pipeline traceback (truncated):\n" + result.traceback[-1500:])

        # --- failure category B: annotation never reached the renderer ------------
        if not calls:
            failures.append(
                "renderer 从未被调用 (renderer never invoked): no render call was recorded, "
                "so annotation delivery cannot be verified."
            )
        else:
            for record in calls:
                if record["error"]:
                    failures.append(
                        f"annotation 缺失 (missing annotation): scene {record['scene_id']} "
                        f"raised {record['error']} - payload was {record['visual']}"
                    )
                else:
                    assert record["annotation_exists"], (
                        f"scene {record['scene_id']}: annotation path does not exist on disk: "
                        f"{record['visual'].get('annotation')}"
                    )
                    assert record["annotation_dir_ok"], (
                        f"scene {record['scene_id']}: annotation was not generated into the "
                        f"resolved asset directory (annotation_policy 'generate' never ran): "
                        f"{record['visual'].get('annotation')}"
                    )

        # --- failure category E: every rendered scene must carry a real annotation -
        #
        # Design contract: resolve_visual_representation(...).payload only *reads*
        # an annotation if one already exists; it does not generate one. Annotation
        # generation is done inside video_pipeline.run()'s unified render loop via
        # auto_annotate_bridge(...). So the end-state validation lives in the
        # renderer-call records (each call's scene.visual.annotation must exist
        # on disk and land in the resolved asset directory), which the B-class
        # assertions above already enforce per-call.
        #
        # This E-class check is a redundant cross-check: for every rendered call,
        # the visual payload must contain an annotation field. If any planned
        # scene was silently skipped, the C-class assertion will surface it.
        for record in calls:
            visual = record.get("visual") or {}
            if "annotation" not in visual:
                failures.append(
                    f"渲染时场景缺少 annotation (render-time scene has no annotation): "
                    f"scene {record['scene_id']} visual_keys={sorted(visual.keys())}"
                )

        # --- failure category C: every scene must be rendered exactly once --------
        if calls:
            assert len(calls) == len(scene_json), (
                f"期望每个计划场景都被渲染一次 (renderer must be called once per planned scene): "
                f"rendered {len(calls)}, planned {len(scene_json)}; rendered ids="
                f"{[record['scene_id'] for record in calls]}"
            )
            assert len({record["scene_id"] for record in calls}) == len(calls), (
                "同一场景被渲染多次或场景 id 重复: "
                f"{[record['scene_id'] for record in calls]}"
            )
            assert all(record["renderer"] == "whiteboard" for record in calls), (
                f"场景未路由到白板 Renderer: {[record['renderer'] for record in calls]}"
            )
            assert all((record["duration_ms"] or 0) > 0 for record in calls), (
                "存在时长非正的渲染场景: "
                f"{[record['duration_ms'] for record in calls]}"
            )

        # --- failure category D: merge must receive one clip per rendered scene ---
        if result.merge_inputs is None:
            failures.append(
                "merge 阶段未被执行 (merge never executed): merge_scenes_main was not called"
            )
        else:
            assert len(result.merge_inputs) == len(scene_json), (
                f"合并输入数量与场景数量不一致 (merge inputs must match planned scene count): "
                f"inputs={len(result.merge_inputs)}, planned={len(scene_json)}"
            )
            for clip in result.merge_inputs:
                assert Path(clip).exists() and Path(clip).stat().st_size > 0, (
                    f"合并输入片段缺失或为空: {clip}"
                )
            assert result.render_mp4.exists() and result.render_mp4.stat().st_size > 0, (
                f"合并产物缺失: {result.render_mp4}"
            )

        if failures:
            raise AssertionError(
                "白板多场景本地素材路径未通过，问题分类如下:\n"
                + "\n".join(f"  - {item}" for item in failures)
            )

        print(f"[ok] {len(calls)} 个场景全部经 auto_annotate_bridge + WhiteboardRenderer 渲染，"
              f"annotation 均为真实生成文件，merge 收到 {len(result.merge_inputs)} 个片段")

    finally:
        if result.tmp_root is not None:
            shutil.rmtree(result.tmp_root, ignore_errors=True)

def main() -> int:
    try:
        test_whiteboard_multiscene_local_asset_path()
    except AssertionError as exc:
        print(f"\n[FAIL] {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"\n[ERROR] 测试执行异常: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        return 2
    print(f"elapsed_sec={time.time() - SCRIPT_START:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
