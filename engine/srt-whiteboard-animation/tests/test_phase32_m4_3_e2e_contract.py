"""Phase 32 / M4-3 端到端生产与产物治理契约测试（T1–T8）。

运行方式（仅使用指定虚拟环境，不联网、不安装依赖）：
    cd /home/hqb/projects/video-factory/engine/srt-whiteboard-animation
    .venv/bin/python tests/test_phase32_m4_3_e2e_contract.py

边界：
- 全部产物写入 M43_WORK_ROOT（默认 /tmp/phase32-m4-3-20261026/）之下，
  不触碰仓库正式输出目录；每次运行的输出目录带独立时间戳后缀，
  因此可重复执行，且不会覆盖历史运行的同名成品。
- TTS 用本地 ffmpeg 合成的静音 mp3 替代（不联网、不调用 Edge TTS）。
- 音频轨合成与最终音画合并走真实 video_pipeline.build_audio_track /
  build_final_video；白板渲染走真实 renderer，因此 M4-1 / M4-2 契约被真实执行。
- 第一次运行人为把第 2 段语音加长 1.2s，用于触发 M4-2 的超窗检测路径。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
SCRIPTS = ENGINE / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# M4-6：工作根目录改为可配置（环境变量优先），默认值仅作本地默认，
# 不再把测试绑死到某个历史临时目录；每次运行的输出目录带时间戳，保证可重复执行。
ROOT = Path(os.environ.get("M43_WORK_ROOT", "/tmp/phase32-m4-3-20261026")).expanduser().resolve()
SUPPORT = ROOT / "e2e-support"
ASSETS = SUPPORT / "assets"
_RUN_TAG = time.strftime("%Y%m%d-%H%M%S")
RUN1 = ROOT / f"e2e-run-a1-{_RUN_TAG}"
RUN2 = ROOT / f"e2e-run-a2-{_RUN_TAG}"
FAIL_DIR = ROOT / f"e2e-run-fail2-{_RUN_TAG}"

import video_pipeline as vp
from story_from_topic import build_story_json, write_story_json
from story_to_srt import write_srt_from_story
from tts.base import TTSProvider

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"


def _ffprobe(path: Path) -> dict:
    result = subprocess.run(
        [FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


class LocalSilentTTS(TTSProvider):
    """本地合成 TTS：ffmpeg 生成时长可控的静音 mp3，不联网。"""

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


class Phase32M43E2EContract(unittest.TestCase):
    evidence: dict = {}

    @classmethod
    def setUpClass(cls):
        # M4-6：不再断言某个固定绝对路径；只要求工作根目录不在仓库内，
        # 避免测试产物污染工程目录（路径本身可由 M43_WORK_ROOT 指定）。
        assert ROOT.is_absolute(), "M43_WORK_ROOT 必须是绝对路径"
        assert not ROOT.is_relative_to(ENGINE), "测试工作目录不能位于仓库内"
        SUPPORT.mkdir(parents=True, exist_ok=True)
        ASSETS.mkdir(exist_ok=True)

        rocket = ENGINE / "output" / "my-rocket" / "rocket.jpg"
        asset_copy = ASSETS / "rocket.jpg"
        if not asset_copy.exists():
            shutil.copyfile(rocket, asset_copy)

        story_path = SUPPORT / "story.json"
        if not story_path.exists():
            story = build_story_json("火箭是如何飞向太空的", "mock")
            write_story_json(story, story_path, allow_overwrite=False)
        srt_path = SUPPORT / "story.srt"
        if not srt_path.exists():
            write_srt_from_story(story_path, srt_path)

        cls.story_json = story_path
        cls.srt = srt_path
        cls.srt_mtime = srt_path.stat().st_mtime
        cls.story_mtime = story_path.stat().st_mtime
        cls.cues = vp.parse_srt(srt_path)
        cls.srt_end_ms = max(vp._subtitle_end_ms(cue) for cue in cls.cues)

        cls.run1_tts = LocalSilentTTS(duration_overrides={2: 1.2})
        cls.run1 = cls._invoke(RUN1, srt_path, cls.run1_tts)
        cls.manifest = json.loads((RUN1 / "run-manifest.json").read_text(encoding="utf-8"))
        cls.final_mp4 = Path(cls.manifest["outputs"]["final_mp4"])
        cls.final_stat = cls.final_mp4.stat() if cls.final_mp4.exists() else None

        cls.evidence.update({
            "story_json": str(story_path),
            "srt": str(srt_path),
            "cue_count": len(cls.cues),
            "srt_end_ms": cls.srt_end_ms,
            "run1_seconds": cls.run1["seconds"],
            "run1_result": cls.run1["result"],
        })
        print(f"\n[T1] run-a1 finished in {cls.run1['seconds']}s -> {cls.final_mp4}")

    @classmethod
    def tearDownClass(cls):
        out = ROOT / f"m4-3-test-evidence-{_RUN_TAG}.json"
        out.write_text(json.dumps(cls.evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[evidence] {out}")

    @staticmethod
    def _invoke(output_dir: Path, srt_path: Path, tts, render_only: bool = False) -> dict:
        original = vp.create_tts_provider
        vp.create_tts_provider = lambda name, **kwargs: tts
        started = time.time()
        try:
            result = vp.main(
                srt_path=srt_path,
                output_dir=output_dir,
                renderer_name="whiteboard",
                tts_name="local-silent",
                asset_roots=[str(ASSETS)],
                render_only=render_only,
            )
        finally:
            vp.create_tts_provider = original
        return {
            "result": None if result is None else str(result),
            "seconds": round(time.time() - started, 2),
        }

    # ---------- T1 ----------

    def test_t1_end_to_end_artifacts(self):
        outputs = self.manifest["outputs"]
        produced = {
            "story_json": self.story_json,
            "srt": self.srt,
            "scene_json": Path(outputs["scene_json"]),
            "audio_track": Path(outputs["audio_track"]),
            "render_video": Path(outputs["render_video"]),
            "final_mp4": Path(outputs["final_mp4"]),
            "manifest": RUN1 / "run-manifest.json",
        }
        for index, clip in enumerate(outputs.get("scene_clips") or [], start=1):
            produced[f"scene_clip_{index:02d}"] = Path(clip)

        missing = [name for name, path in produced.items() if not Path(path).exists()]
        self.assertEqual(missing, [], f"缺少产物: {missing}")
        self.assertEqual(self.manifest["status"], "success")
        self.assertGreater(Path(outputs["scene_json"]).stat().st_size, 0)
        self.evidence["t1"] = {
            "produced": {k: str(v) for k, v in produced.items()},
            "sizes_mb": {
                k: round(Path(v).stat().st_size / 1048576, 3) for k, v in produced.items()
            },
            "manifest_status": self.manifest["status"],
            "scene_count": len(outputs.get("scene_clips") or []),
        }

    # ---------- T2 ----------

    def test_t2_final_media_probe(self):
        probe = _ffprobe(self.final_mp4)
        streams = probe["streams"]
        video = [s for s in streams if s["codec_type"] == "video"]
        audio = [s for s in streams if s["codec_type"] == "audio"]
        subtitles = [s for s in streams if s["codec_type"] == "subtitle"]
        self.assertEqual(len(video), 1)
        self.assertEqual(len(audio), 1)
        self.assertEqual(len(subtitles), 0, "字幕为烧录，不应存在独立字幕轨")
        self.evidence["t2"] = {
            "video_codec": video[0]["codec_name"],
            "width": video[0]["width"],
            "height": video[0]["height"],
            "video_duration_ms": vp.ffprobe_stream_duration_ms(self.final_mp4, "v:0"),
            "audio_codec": audio[0]["codec_name"],
            "audio_duration_ms": vp.ffprobe_stream_duration_ms(self.final_mp4, "a:0"),
            "container_duration": probe["format"].get("duration"),
            "subtitle_streams": len(subtitles),
            "size_bytes": self.final_mp4.stat().st_size,
        }

    # ---------- T3 ----------

    def test_t3_subtitles_and_narration(self):
        self.assertGreaterEqual(len(self.cues), 1)
        self.assertTrue(all(
            vp._subtitle_end_ms(cue) > vp._subtitle_start_ms(cue) for cue in self.cues
        ))
        self.assertEqual(vp._subtitle_end_ms(self.cues[-1]), self.srt_end_ms)

        outputs = self.manifest["outputs"]
        audio_track = Path(outputs["audio_track"])
        self.assertGreater(audio_track.stat().st_size, 0)
        track_ms = vp.ffprobe_stream_duration_ms(audio_track, "a:0")
        final_audio_ms = vp.ffprobe_stream_duration_ms(self.final_mp4, "a:0")

        timeline = self.manifest.get("audio_timeline") or {}
        self.assertNotEqual(timeline.get("status"), "unknown", timeline)
        self.assertIsNotNone(timeline.get("audio_end_ms"))
        self.assertGreaterEqual(
            final_audio_ms,
            int(timeline["audio_end_ms"]) - 200,
            "最终音频流短于语音结束点，说明发生了静默截断",
        )
        self.assertLessEqual(abs(final_audio_ms - track_ms), 200)

        self.evidence["t3"] = {
            "cue_count": len(self.cues),
            "srt_end_ms": self.srt_end_ms,
            "audio_track_ms": track_ms,
            "final_audio_ms": final_audio_ms,
            "narrated_end_ms": timeline.get("audio_end_ms"),
            "subtitle_mode": "burned_in (build_final_video subtitles filter)",
            "standalone_subtitle_stream": False,
        }

    # ---------- T4 ----------

    def test_t4_m41_duration_contract(self):
        durations = [int(v) for v in (self.manifest.get("render_durations_ms") or [])]
        self.assertTrue(durations, "流水线未记录场景渲染目标时长")
        self.assertEqual(sum(durations), self.srt_end_ms, "场景目标时长之和应等于 SRT 末尾")

        fit = self.manifest.get("clip_fit") or []
        self.assertEqual(len(fit), len(durations), "每个白板场景都应记录时长对齐事实")
        mismatched = [
            item for item in fit
            if item.get("adjusted_ms") is None
            or abs(int(item["adjusted_ms"]) - int(item["target_ms"])) > 45
        ]
        self.assertEqual(mismatched, [], "存在未对齐到目标时长的场景片段")

        self.evidence["t4"] = {
            "render_durations_ms": durations,
            "srt_end_ms": self.srt_end_ms,
            "clip_fit": fit,
        }

    # ---------- T5 ----------

    def test_t5_m42_audio_video_contract(self):
        timeline = self.manifest.get("audio_timeline") or {}
        entries = timeline.get("entries") or []
        video_ms = vp.ffprobe_stream_duration_ms(self.final_mp4, "v:0")
        audio_ms = vp.ffprobe_stream_duration_ms(self.final_mp4, "a:0")
        end_delta = abs(video_ms - audio_ms)
        self.assertLessEqual(end_delta, 200, f"音画结束差 {end_delta}ms 超过 200ms")

        self.evidence["t5"] = {
            "entries": entries,
            "overruns": timeline.get("overruns"),
            "overlaps": timeline.get("overlaps"),
            "srt_end_ms": timeline.get("srt_end_ms"),
            "audio_end_ms": timeline.get("audio_end_ms"),
            "tail_gap_ms": timeline.get("tail_gap_ms"),
            "audio_beyond_srt_ms": timeline.get("audio_beyond_srt_ms"),
            "final_video_ms": video_ms,
            "final_audio_ms": audio_ms,
            "end_delta_ms": end_delta,
            "final_media": self.manifest.get("final_media"),
        }

    # ---------- T6 ----------

    def test_t6_output_isolation(self):
        artifacts = self.manifest.get("artifacts", [])
        self.assertTrue(artifacts)
        outside = [
            item["path"] for item in artifacts
            if not Path(item["path"]).resolve().is_relative_to(ROOT)
        ]
        self.assertEqual(outside, [], f"产物越出临时目录: {outside}")
        self.assertEqual(Path(self.manifest["output_dir"]).resolve(), RUN1.resolve())
        self.assertFalse(str(self.final_mp4).startswith(str(ENGINE / "output")))
        self.assertEqual(self.srt.stat().st_mtime, self.srt_mtime, "SRT 输入被修改")
        self.assertEqual(self.story_json.stat().st_mtime, self.story_mtime, "story.json 被修改")
        self.evidence["t6"] = {
            "artifact_count": len(artifacts),
            "outside_temp": outside,
            "srt_untouched": True,
            "story_untouched": True,
        }

    # ---------- T7 ----------

    def test_t7_failure_diagnostics(self):
        missing_srt = FAIL_DIR / "missing.srt"
        with self.assertRaises(FileNotFoundError):
            vp.main(
                srt_path=missing_srt,
                output_dir=FAIL_DIR,
                renderer_name="whiteboard",
                tts_name="local-silent",
                asset_roots=[str(ASSETS)],
            )
        manifest_path = FAIL_DIR / "run-manifest.json"
        self.assertTrue(manifest_path.exists(), "失败时未生成诊断清单")
        failed = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(failed["status"], "failed")
        self.assertIsNotNone(failed["error"])
        self.assertEqual(failed["error"]["type"], "FileNotFoundError")
        self.assertEqual(failed["error"]["stage"], "validate_srt")
        self.evidence["t7"] = {
            "manifest": str(manifest_path),
            "status": failed["status"],
            "stage": failed["error"]["stage"],
            "type": failed["error"]["type"],
            "message": failed["error"]["message"],
        }

    # ---------- T8 ----------

    def test_t8_repeat_run_safety(self):
        short = SUPPORT / "short.srt"
        if not short.exists():
            short.write_text(
                "1\n00:00:00,000 --> 00:00:02,000\n火箭停在发射台上。\n",
                encoding="utf-8",
            )
        before = self.final_stat
        run2 = self._invoke(RUN2, short, LocalSilentTTS())
        run2_manifest = json.loads((RUN2 / "run-manifest.json").read_text(encoding="utf-8"))
        run2_final = Path(run2_manifest["outputs"]["final_mp4"])

        self.assertTrue(run2_final.exists())
        self.assertNotEqual(run2_final.resolve(), self.final_mp4.resolve())
        self.assertGreater(run2_final.stat().st_size, 0)
        after = self.final_mp4.stat()
        self.assertEqual(after.st_size, before.st_size, "第二次运行覆盖了第一次的最终 MP4")
        self.assertEqual(after.st_mtime, before.st_mtime, "第二次运行改写了第一次产物")
        self.evidence["t8"] = {
            "run1_final": str(self.final_mp4),
            "run1_size_bytes": before.st_size,
            "run2_final": str(run2_final),
            "run2_size_bytes": run2_final.stat().st_size,
            "run2_seconds": run2["seconds"],
            "run1_untouched": True,
        }


if __name__ == "__main__":
    unittest.main(verbosity=2)
