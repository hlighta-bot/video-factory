"""Phase 32 / M4-6 定向测试：产物覆盖保护 + manifest timing 可追溯性（T1–T14）。

运行方式（仅使用指定虚拟环境，不联网、不安装依赖、不调用 FFmpeg/TTS）：
    cd /home/hqb/projects/video-factory/engine/srt-whiteboard-animation
    M46_WORK_ROOT=/tmp/phase32-m4-6-20261026/tests \
        .venv/bin/python tests/test_phase32_m4_6_overwrite_and_timing.py

边界：
- 全部产物写入 M46_WORK_ROOT（默认 /tmp/phase32-m4-6-20261026/tests），不写仓库 output/。
- 用 stub 替换 video_pipeline._run_pipeline，只验证 main() 的控制流与写盘契约，
  不触发白板渲染、TTS、FFmpeg 或完整媒体流水线。
- 防覆盖证据一律比对文件字节（sha256 / bytes），不依赖日志文字。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import time
import unittest
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
SCRIPTS = ENGINE / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

WORK_ROOT = Path(
    os.environ.get("M46_WORK_ROOT", "/tmp/phase32-m4-6-20261026/tests")
).expanduser().resolve()

import video_pipeline as vp


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class M46Base(unittest.TestCase):
    """每个用例独立输出目录；stub 记录调用情况，不产生任何媒体。"""

    def setUp(self):
        WORK_ROOT.mkdir(parents=True, exist_ok=True)
        self.case_dir = WORK_ROOT / f"{self._testMethodName}-{time.strftime('%H%M%S')}-{os.getpid()}"
        self.case_dir.mkdir(parents=True, exist_ok=False)
        self.out_dir = self.case_dir / "out"
        self.out_dir.mkdir()
        self.srt = self.case_dir / "input.srt"
        self.srt.write_text("1\n00:00:00,000 --> 00:00:02,000\n测试。\n", encoding="utf-8")

        self.calls = []
        self.stub_error = None
        self.stub_timing = None       # None=不写 timing；Path=写并登记；str("ghost")=登记不存在路径
        self.stub_clip_fit = None
        self._original = vp._run_pipeline
        vp._run_pipeline = self._stub_run_pipeline

    def tearDown(self):
        vp._run_pipeline = self._original

    def _stub_run_pipeline(self, srt_path, **kwargs):
        state = kwargs.get("state")
        out = Path(kwargs.get("output_dir")).resolve()
        self.calls.append({"srt": str(srt_path), "output_dir": str(out)})
        if self.stub_error is not None:
            state["stage"] = "stub"
            raise self.stub_error

        final = out / f"{out.name}-final.mp4"
        final.write_bytes(b"FINAL-" + str(time.time()).encode())
        state["final_mp4"] = str(final)
        state["stage"] = "stub_done"

        if self.stub_clip_fit is not None:
            state["clip_fit"] = list(self.stub_clip_fit)

        if isinstance(self.stub_timing, Path):
            self.stub_timing.parent.mkdir(parents=True, exist_ok=True)
            self.stub_timing.write_text('{"elements": 3}', encoding="utf-8")
            state["timing_files"] = [str(self.stub_timing)]
        elif isinstance(self.stub_timing, str):
            state["timing_files"] = [self.stub_timing]
        return final

    def _run_main(self, **kwargs):
        return vp.main(
            srt_path=kwargs.pop("srt_path", self.srt),
            output_dir=kwargs.pop("output_dir", self.out_dir),
            **kwargs,
        )


class OverwriteProtection(M46Base):
    # ---------- T1 ----------
    def test_t1_fresh_dir_passes(self):
        result = self._run_main(renderer_name="whiteboard", tts_name="stub")
        self.assertEqual(len(self.calls), 1, "全新目录应正常进入流水线")
        self.assertTrue(Path(result).exists())
        self.assertTrue((self.out_dir / "run-manifest.json").exists())

    # ---------- T2 ----------
    def test_t2_existing_final_mp4_rejected_bytes_intact(self):
        target = self.out_dir / f"{self.out_dir.name}-final.mp4"
        target.write_bytes(b"OLD-FINAL-CONTENT")
        before = _sha(target)
        with self.assertRaises(RuntimeError) as ctx:
            self._run_main()
        self.assertIn(str(target), str(ctx.exception))
        self.assertEqual(_sha(target), before, "旧最终 MP4 字节被改动")
        self.assertEqual(len(self.calls), 0, "冲突时不得进入流水线")

    # ---------- T3 ----------
    def test_t3_existing_intermediate_rejected_bytes_intact(self):
        clip = self.out_dir / f"{self.out_dir.name}-scene-01.mp4"
        clip.write_bytes(b"OLD-SCENE-CLIP")
        before = _sha(clip)
        with self.assertRaises(RuntimeError) as ctx:
            self._run_main()
        self.assertIn(str(clip), str(ctx.exception))
        self.assertEqual(_sha(clip), before, "旧场景片段字节被改动")
        self.assertEqual(len(self.calls), 0)

    # ---------- T4 ----------
    def test_t4_existing_manifest_rejected_bytes_intact(self):
        manifest = self.out_dir / "run-manifest.json"
        manifest.write_bytes(b'{"status":"old-run"}')
        before = _sha(manifest)
        with self.assertRaises(RuntimeError):
            self._run_main()
        self.assertEqual(_sha(manifest), before, "旧 manifest 被覆盖（含失败清单回写）")

    # ---------- T5 ----------
    def test_t5_conflict_writes_no_other_targets(self):
        audio = self.out_dir / f"{self.out_dir.name}-voice.m4a"
        audio.write_bytes(b"OLD-AUDIO")
        audio_before = _sha(audio)
        with self.assertRaises(RuntimeError):
            self._run_main()
        for name in (
            "scene.json",
            f"{self.out_dir.name}-render.mp4",
            f"{self.out_dir.name}-whiteboard.mp4",
            f"{self.out_dir.name}-final.mp4",
        ):
            self.assertFalse((self.out_dir / name).exists(), f"冲突时不应生成 {name}")
        self.assertEqual(_sha(audio), audio_before)
        self.assertEqual(len(self.calls), 0)

    # ---------- T6 ----------
    def test_t6_unrelated_files_do_not_block(self):
        notes = self.out_dir / "notes.txt"
        notes.write_bytes(b"UNRELATED")
        other = self.out_dir / "another-project-final.mp4"
        other.write_bytes(b"OTHER-PROJECT")
        notes_before, other_before = _sha(notes), _sha(other)

        self._run_main()
        self.assertEqual(len(self.calls), 1, "无关文件不应导致误拒")
        self.assertEqual(_sha(notes), notes_before)
        self.assertEqual(_sha(other), other_before)

    # ---------- T7 ----------
    def test_t7_repeat_run_does_not_silently_overwrite(self):
        self._run_main()
        final = self.out_dir / f"{self.out_dir.name}-final.mp4"
        before = _sha(final)
        with self.assertRaises(RuntimeError) as ctx:
            self._run_main()
        self.assertIn(str(final), str(ctx.exception))
        self.assertEqual(_sha(final), before, "重复运行静默覆盖了已有成品")
        self.assertEqual(len(self.calls), 1, "第二次运行不应进入流水线")

    # ---------- T8 ----------
    def test_t8_conflict_does_not_delete_old_files(self):
        render = self.out_dir / f"{self.out_dir.name}-render.mp4"
        render.write_bytes(b"OLD-RENDER")
        render_before = _sha(render)
        with self.assertRaises(RuntimeError):
            self._run_main()
        self.assertTrue(self.out_dir.is_dir(), "冲突时输出目录被删除")
        self.assertEqual(_sha(render), render_before, "冲突时旧文件被删除或改写")
        self.assertEqual(sorted(p.name for p in self.out_dir.iterdir()),
                         [f"{self.out_dir.name}-render.mp4"])

    # ---------- 补充：pipeline 内部异常不删除既有旧产物 ----------
    def test_t8b_internal_failure_keeps_manifest_and_files(self):
        self.stub_error = RuntimeError("boom")
        with self.assertRaises(RuntimeError):
            self._run_main()
        manifest = self.out_dir / "run-manifest.json"
        self.assertTrue(manifest.exists())
        failed = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error"]["type"], "RuntimeError")


class TimingManifestReference(M46Base):
    def _timing_path(self):
        return (
            self.out_dir
            / ".timing"
            / f"{self.out_dir.name}-scene-00.fit5000.deadbeef.annotation.json"
        )

    # ---------- T9 ----------
    def test_t9_manifest_references_relative_timing_path(self):
        self.stub_timing = self._timing_path()
        self._run_main()
        manifest = json.loads((self.out_dir / "run-manifest.json").read_text(encoding="utf-8"))
        entries = manifest.get("timing_diagnostics")
        self.assertEqual(len(entries), 1, entries)
        entry = entries[0]
        self.assertTrue(entry["path"].startswith(".timing/"), entry["path"])
        self.assertNotIn(str(WORK_ROOT), entry["path"], "引用不应暴露绝对工作路径")
        self.assertFalse(Path(entry["path"]).is_absolute())
        self.assertEqual(entry["path"], self._timing_path().relative_to(self.out_dir).as_posix())

    # ---------- T10 ----------
    def test_t10_reference_points_to_existing_file(self):
        timing = self._timing_path()
        self.stub_timing = timing
        self._run_main()
        manifest = json.loads((self.out_dir / "run-manifest.json").read_text(encoding="utf-8"))
        entry = manifest["timing_diagnostics"][0]
        resolved = self.out_dir / entry["path"]
        self.assertTrue(resolved.exists(), "manifest 引用了不存在的文件")
        self.assertTrue(entry["exists"])
        self.assertEqual(entry["size_bytes"], resolved.stat().st_size)
        self.assertEqual(resolved.read_bytes(), b'{"elements": 3}')

    # ---------- T11 ----------
    def test_t11_no_timing_file_no_fake_reference(self):
        self.stub_timing = None
        self._run_main()
        manifest = json.loads((self.out_dir / "run-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest.get("timing_diagnostics"), [])
        self.assertFalse((self.out_dir / ".timing").exists(), "不应凭空创建诊断目录")

        ghost_dir = self.case_dir / "ghost"
        ghost_dir.mkdir()
        ghost_out = ghost_dir / "out"
        ghost_out.mkdir()
        ghost_path = ghost_out / ".timing" / "ghost.annotation.json"  # 故意不创建
        self.stub_timing = str(ghost_path)
        self._run_main(output_dir=ghost_out)
        ghost_manifest = json.loads((ghost_out / "run-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(ghost_manifest.get("timing_diagnostics"), [],
                         "不存在的 timing 文件不得写入引用")

    # ---------- T12 ----------
    def test_t12_existing_manifest_fields_remain_compatible(self):
        self.stub_timing = self._timing_path()
        self.stub_clip_fit = [{
            "label": "scene-00", "target_ms": 5000, "adjusted_ms": 5002,
            "renderer_plan": {"mode": "fitted", "elements": 3},
        }]
        self._run_main()
        manifest = json.loads((self.out_dir / "run-manifest.json").read_text(encoding="utf-8"))

        for key in ("phase", "status", "started_at", "finished_at", "output_dir",
                    "stage_reached", "renderer", "inputs", "outputs",
                    "render_durations_ms", "clip_fit", "artifacts",
                    "final_media", "audio_timeline", "error"):
            self.assertIn(key, manifest, f"既有 manifest 字段丢失: {key}")
        self.assertEqual(manifest["clip_fit"], self.stub_clip_fit)
        self.assertEqual(manifest["status"], "success")
        self.assertIsNone(manifest["error"])
        self.assertEqual(manifest["timing_diagnostics"][0]["path"],
                         self._timing_path().relative_to(self.out_dir).as_posix())


class M43Portability(M46Base):
    TEST_PATH = ENGINE / "tests" / "test_phase32_m4_3_e2e_contract.py"

    # ---------- T13 ----------
    def test_t13_m43_hardcoded_path_removed_and_root_overridable(self):
        source = self.TEST_PATH.read_text(encoding="utf-8")
        self.assertIn("M43_WORK_ROOT", source, "M4-3 测试未提供可覆盖的工作根目录")
        self.assertNotIn('assert str(ROOT) == "/tmp/phase32-m4-3-20261026"', source,
                         "仍存在把工作目录绑死到历史路径的断言")

        target_root = self.case_dir / "m43-root"
        previous = os.environ.get("M43_WORK_ROOT")
        os.environ["M43_WORK_ROOT"] = str(target_root)
        try:
            spec = importlib.util.spec_from_file_location("m43_contract_probe", self.TEST_PATH)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)  # 仅执行模块顶层，不触发 setUpClass / 媒体
        finally:
            if previous is None:
                os.environ.pop("M43_WORK_ROOT", None)
            else:
                os.environ["M43_WORK_ROOT"] = previous

        self.assertEqual(module.ROOT, target_root.resolve())
        for run_dir in (module.RUN1, module.RUN2, module.FAIL_DIR):
            self.assertTrue(str(run_dir).startswith(str(target_root.resolve())))
        # 运行目录必须带一次性后缀，避免与历史运行撞名（否则防覆盖会直接拒绝重跑）
        self.assertNotEqual(module.RUN1, module.ROOT / "e2e-run-a1")
        self.assertNotEqual(module.RUN2, module.ROOT / "e2e-run-a2")
        self.assertNotEqual(module.FAIL_DIR, module.ROOT / "e2e-run-fail2")
        self.assertNotEqual(module.RUN1, module.RUN2)

    # ---------- T14 ----------
    @unittest.skipUnless(
        os.environ.get("M46_ALLOW_MEDIA") == "1",
        "UNVERIFIED：完整 M4-3 媒体回归需 FFmpeg/真实媒体生成，本阶段明确禁止，未执行",
    )
    def test_t14_full_m43_media_regression(self):
        raise AssertionError("该用例仅用于标记未验证项，未获授权时不执行")


if __name__ == "__main__":
    unittest.main(verbosity=2)
