"""Focused tests for the Audio8 TTS client contract."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tts.audio8 import Audio8TTSProvider, _rate_to_speed
from video_pipeline import _tts_diagnostics


class _FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return b"ID3" + b"x" * 2048


class Audio8TTSChecks(unittest.TestCase):
    def test_rate_maps_to_tts_hub_speed(self):
        self.assertEqual(_rate_to_speed("-10%"), 0.9)
        self.assertEqual(_rate_to_speed("+15%"), 1.15)
        with self.assertRaises(ValueError):
            _rate_to_speed("fast")

    def test_request_uses_audio8_strict_route_and_default_voice(self):
        with tempfile.TemporaryDirectory(prefix="vf-audio8-") as temp_dir:
            provider = Audio8TTSProvider(start_services=False)
            provider._ready = True
            output = Path(temp_dir) / "001.mp3"
            with patch("tts.audio8.urllib.request.urlopen", return_value=_FakeResponse()) as open_url:
                provider.synthesize("这是一段语音测试。", output)

            self.assertTrue(output.is_file())
            self.assertGreater(output.stat().st_size, 1024)
            request = open_url.call_args.args[0]
            payload = json.loads(request.data)
            self.assertEqual(payload["provider"], "audio8")
            self.assertEqual(payload["voice"], "default")
            self.assertEqual(payload["response_format"], "mp3")
            self.assertEqual(payload["speed"], 0.9)
            self.assertFalse(payload["allow_fallback"])
            self.assertEqual(provider.request_count, 1)
            self.assertEqual(provider.chunk_count, 1)

    def test_diagnostics_record_actual_audio8_backend_and_ignored_pitch(self):
        provider = Audio8TTSProvider(start_services=False)
        result = _tts_diagnostics("audio8", provider)
        self.assertEqual(result["backend"], "audio8")
        self.assertEqual(result["voice"], "default")
        self.assertEqual(result["service_url"], "http://127.0.0.1:18084")
        self.assertEqual(result["sample_rate"], 44100)
        self.assertTrue(any("does not support pitch" in item for item in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
