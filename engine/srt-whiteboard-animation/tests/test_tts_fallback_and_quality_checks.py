"""Focused regression checks for automatic TTS fallback and run diagnostics."""

from __future__ import annotations

import json
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from renderers.cinematic import CinematicRenderer
from scene.visual_spec import adapt_whiteboard
from story.router import create_story_provider
from tts.auto import AutoTTSProvider
from tts.espeak import EspeakTTSProvider
from video_pipeline import (
    _assess_final_media,
    _generate_visual_review,
    _inspect_scene_coverage,
    _inspect_scene_semantics,
    generate_all_tts,
)


class FallbackAndQualityChecks(unittest.TestCase):
    def test_espeak_rate_maps_percentage_to_wpm(self):
        provider = EspeakTTSProvider()
        self.assertEqual(provider.voice, "zh")
        self.assertEqual(provider._speed_wpm("-10%"), 180)
        self.assertEqual(provider._speed_wpm("+10%"), 220)
        with self.assertRaises(ValueError):
            provider._speed_wpm("fast")

    def test_mock_provider_starts_without_gemini_sdk(self):
        with patch.dict(sys.modules, {"google": None}):
            provider = create_story_provider("mock")
            self.assertEqual(provider.name, "mock")
            with self.assertRaisesRegex(RuntimeError, "prepare_env.py --provider gemini"):
                create_story_provider("gemini", api_key="test-key", load_env=False)

    def test_auto_provider_uses_audio8_then_falls_back_once_and_stays_local(self):
        with tempfile.TemporaryDirectory(prefix="vf-auto-tts-") as temp_dir:
            output = Path(temp_dir) / "001.mp3"
            with (
                patch("tts.auto.Audio8TTSProvider.synthesize", side_effect=RuntimeError("service unavailable")) as audio8,
                patch("tts.auto.EspeakTTSProvider.synthesize", side_effect=lambda text, path: Path(path)) as offline,
            ):
                provider = AutoTTSProvider()
                provider.synthesize("第一句", output)
                provider.synthesize("第二句", output)

            self.assertEqual(audio8.call_count, 1)
            self.assertEqual(offline.call_count, 2)
            self.assertTrue(provider.using_offline)

    def test_previous_cues_are_regenerated_after_mid_job_fallback(self):
        class SwitchingProvider:
            using_offline = False

            def __init__(self):
                self.calls = []

            def synthesize(self, text, output_path):
                if text == "第二句":
                    self.using_offline = True
                self.calls.append((text, str(output_path), self.using_offline))
                Path(output_path).write_bytes(b"offline" if self.using_offline else b"edge")
                return Path(output_path)

        with tempfile.TemporaryDirectory(prefix="vf-tts-cues-") as temp_dir:
            provider = SwitchingProvider()
            subtitles = [
                {"id": 1, "start": "00:00:00,000", "end": "00:00:01,000", "text": "第一句"},
                {"id": 2, "start": "00:00:01,000", "end": "00:00:02,000", "text": "第二句"},
                {"id": 3, "start": "00:00:02,000", "end": "00:00:03,000", "text": "第三句"},
            ]
            files = generate_all_tts(provider, subtitles, Path(temp_dir))

            self.assertEqual(len(files), 3)
            self.assertEqual(Path(files[0]["file"]).read_bytes(), b"offline")
            self.assertEqual(Path(files[1]["file"]).read_bytes(), b"offline")
            self.assertEqual(Path(files[2]["file"]).read_bytes(), b"offline")
            self.assertEqual(len(provider.calls), 4)  # cue 1 is regenerated

    def test_scene_coverage_detects_missing_and_duplicate_cues(self):
        with tempfile.TemporaryDirectory(prefix="vf-scene-check-") as temp_dir:
            scene_path = Path(temp_dir) / "scene.json"
            scene_path.write_text(
                json.dumps([{"sceneId": "scene-01", "subtitles": [{"id": 1}, {"id": 2}]}]),
                encoding="utf-8",
            )
            good = _inspect_scene_coverage(scene_path, ["1", "2"])
            self.assertEqual(good["status"], "passed")

            scene_path.write_text(
                json.dumps([{"sceneId": "scene-01", "subtitles": [{"id": 1}, {"id": 1}]}]),
                encoding="utf-8",
            )
            bad = _inspect_scene_coverage(scene_path, ["1", "2"])
            self.assertEqual(bad["status"], "failed")
            self.assertTrue(any("duplicate_subtitle_ids" in item for item in bad["findings"]))
            self.assertTrue(any("missing_subtitle_ids" in item for item in bad["findings"]))

    def test_scene_semantics_flags_generic_subject_but_accepts_resolved_fallback(self):
        with tempfile.TemporaryDirectory(prefix="vf-scene-semantics-") as temp_dir:
            scene_path = Path(temp_dir) / "scene.json"
            scene_path.write_text(json.dumps([
                {"sceneId": "generic", "visual": {"intent": "story_scene", "visual_plan": {"subject": "scene"}}},
                {"sceneId": "cloud", "visual": {"intent": "story_scene", "visual_plan": {
                    "subject": "scene", "semantic": {"subject": "cloud"}
                }}},
            ]), encoding="utf-8")
            report = _inspect_scene_semantics(scene_path)
            self.assertEqual(report["status"], "review_required")
            self.assertEqual(report["generic_scene_count"], 1)
            self.assertEqual(report["findings"][0]["scene"], "generic")

            facts = {
                "exists": True,
                "size_bytes": 100,
                "video_stream_ms": 1000,
                "audio_stream_ms": None,
            }
            quality = _assess_final_media(facts, audio_expected=False, scene_semantics=report)
            self.assertIn("scene_semantics_incomplete", quality["findings"])

    def test_media_check_requires_audio_when_expected(self):
        facts = {
            "exists": True,
            "size_bytes": 100,
            "video_stream_ms": 1000,
            "audio_stream_ms": None,
        }
        self.assertEqual(
            _assess_final_media(facts, audio_expected=True)["status"],
            "failed",
        )
        self.assertEqual(
            _assess_final_media(facts, audio_expected=False)["status"],
            "passed",
        )

    def test_media_check_flags_timeline_conflicts_for_review(self):
        facts = {
            "exists": True,
            "size_bytes": 100,
            "video_stream_ms": 1000,
            "audio_stream_ms": 1000,
            "end_delta_within_200ms": True,
        }
        result = _assess_final_media(
            facts,
            audio_expected=True,
            timeline_conflicts=[{"kind": "large_shift"}],
        )
        self.assertEqual(result["status"], "review_required")
        self.assertIn("audio_timeline_conflicts", result["findings"])

    def test_media_check_flags_visual_anomaly_for_review(self):
        facts = {
            "exists": True,
            "size_bytes": 100,
            "video_stream_ms": 1000,
            "audio_stream_ms": None,
        }
        result = _assess_final_media(
            facts,
            audio_expected=False,
            visual_review={"status": "review_required", "findings": ["near_black"]},
        )
        self.assertEqual(result["status"], "review_required")
        self.assertIn("visual_anomaly_detected", result["findings"])

    def test_visual_review_makes_contact_sheet_and_flags_black_frames(self):
        with tempfile.TemporaryDirectory(prefix="vf-visual-review-") as temp_dir:
            output_dir = Path(temp_dir) / "job"
            output_dir.mkdir()
            video_path = Path(temp_dir) / "black.mp4"
            subprocess.run(
                [
                    "ffmpeg", "-nostdin", "-y", "-v", "error",
                    "-f", "lavfi", "-i", "color=c=black:s=320x240:r=10:d=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            report = _generate_visual_review(video_path, output_dir, 1000)
            self.assertEqual(report["status"], "review_required")
            self.assertTrue(Path(report["contact_sheet"]).is_file())
            self.assertTrue(any("near_black" in finding for finding in report["findings"]))

    def test_cinematic_title_uses_graceful_limit(self):
        scene = SimpleNamespace(
            visual={},
            narration="秦始皇下令连接北方各段城墙。",
            subtitles=[],
        )
        title = CinematicRenderer._scene_title(CinematicRenderer.__new__(CinematicRenderer), scene)
        self.assertEqual(title, "秦始皇下令连接北方各段城墙。")

    def test_visual_plan_values_win_over_generic_semantics(self):
        spec = adapt_whiteboard({
            "id": "rocket-01",
            "visual": {
                "intent": "rocket_launch",
                "visual_plan": {
                    "subject": "rocket",
                    "action": "launch",
                    "environment": "sky",
                    "semantic": {
                        "subject": "scene",
                        "action": "show",
                        "environment": "scene",
                    },
                },
            },
        })
        self.assertIn({"type": "rocket", "role": "subject"}, spec.elements)
        self.assertIn({"type": "environment", "value": "sky"}, spec.elements)
        self.assertIn({"type": "action", "value": "launch"}, spec.elements)


if __name__ == "__main__":
    unittest.main()
