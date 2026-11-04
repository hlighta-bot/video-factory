"""Resilience tests: CLI --tts forwarding, gemini retry/model failover, visual setting chain.

Purpose:
- Guard the bug where story_from_topic forwarded ``--tts None`` to story_pipeline
  and argparse rejected it (invalid choice: None).
- Guard gemini transient-error retries plus GEMINI_MODEL_FALLBACKS model failover.
- Guard the visual setting provider fallback chain (zhipu -> gemini -> openai).
All tests stay offline.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

import visual_setting
from story.gemini_provider import GeminiStoryProvider
import story_from_topic as sft


class _Models:
    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append(model)
        action = self.behaviour(model, len(self.calls))
        if isinstance(action, Exception):
            raise action
        return type("Resp", (), {"text": action})()


class _Client:
    def __init__(self, behaviour):
        self.models = _Models(behaviour)


class GeminiResilienceTests(unittest.TestCase):
    def test_failover_to_next_model_after_transient_errors(self):
        client = _Client(
            lambda model, _n: RuntimeError("503 UNAVAILABLE. high demand")
            if model == "primary-model"
            else '{"ok": true}'
        )
        provider = GeminiStoryProvider(
            api_key="k",
            model="primary-model",
            fallback_models="backup-model",
            retries=1,
            client=client,
            load_env=False,
        )
        self.assertEqual(provider.models, ["primary-model", "backup-model"])
        self.assertEqual(provider.generate("topic"), '{"ok": true}')
        # primary: 1 initial + 1 retry, then the backup once
        self.assertEqual(client.models.calls, ["primary-model", "primary-model", "backup-model"])

    def test_success_on_first_attempt_skips_fallbacks(self):
        client = _Client(lambda model, _n: "text")
        provider = GeminiStoryProvider(
            api_key="k",
            model="primary-model",
            fallback_models="backup-model",
            retries=2,
            client=client,
            load_env=False,
        )
        self.assertEqual(provider.generate("topic"), "text")
        self.assertEqual(client.models.calls, ["primary-model"])

    def test_non_transient_error_does_not_failover(self):
        client = _Client(lambda model, _n: RuntimeError("404 NOT_FOUND"))
        provider = GeminiStoryProvider(
            api_key="k",
            model="primary-model",
            fallback_models="backup-model",
            retries=2,
            client=client,
            load_env=False,
        )
        with self.assertRaises(RuntimeError) as ctx:
            provider.generate("topic")
        self.assertIn("404 NOT_FOUND", str(ctx.exception))
        self.assertEqual(client.models.calls, ["primary-model"])

    def test_all_models_exhausted_raises(self):
        client = _Client(lambda model, _n: RuntimeError("503 UNAVAILABLE"))
        provider = GeminiStoryProvider(
            api_key="k",
            model="primary-model",
            fallback_models="backup-model",
            retries=0,
            client=client,
            load_env=False,
        )
        with self.assertRaises(RuntimeError):
            provider.generate("topic")
        self.assertEqual(client.models.calls, ["primary-model", "backup-model"])

    def test_chat_uses_system_instruction(self):
        seen = {}

        class _ChatClient:
            def __init__(self):
                outer = self

                class _M:
                    def generate_content(self, model, contents, config):
                        seen["model"] = model
                        seen["contents"] = contents
                        seen["system"] = config.system_instruction
                        return type("Resp", (), {"text": "{}"})()

                self.models = _M()

        provider = GeminiStoryProvider(
            api_key="k",
            model="m",
            fallback_models="",
            retries=0,
            client=_ChatClient(),
            load_env=False,
        )
        provider.chat("SYS", "USER")
        self.assertEqual(seen["system"], "SYS")
        self.assertEqual(seen["contents"], "USER")


class DispatchTtsTests(unittest.TestCase):
    def _dispatch(self, tts_name):
        captured = {}

        def fake_main(argv):
            captured["argv"] = argv
            return 0

        original = sft.story_pipeline_main
        sft.story_pipeline_main = fake_main
        try:
            sft._dispatch_to_video_pipeline(
                story_path=Path("/tmp/out/story.json"),
                output_dir=Path("/tmp/out"),
                srt_output="/tmp/out/story.srt",
                chars_per_second=5.0,
                pause_ms=250,
                renderer="auto",
                tts_name=tts_name,
                render_only=False,
                env_py=None,
                asset_roots=[],
                image="",
                image_provider="zhipu",
            )
        finally:
            sft.story_pipeline_main = original
        return captured["argv"]

    def test_none_tts_is_not_forwarded(self):
        argv = self._dispatch(None)
        self.assertNotIn("--tts", argv)

    def test_explicit_tts_is_forwarded(self):
        argv = self._dispatch("auto")
        self.assertIn("--tts", argv)
        self.assertEqual(argv[argv.index("--tts") + 1], "auto")


class VisualSettingChainTests(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("VISUAL_SETTING_PROVIDER")
        os.environ["VISUAL_SETTING_PROVIDER"] = "zhipu,gemini,openai"

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("VISUAL_SETTING_PROVIDER", None)
        else:
            os.environ["VISUAL_SETTING_PROVIDER"] = self._saved

    def test_falls_back_to_next_provider(self):
        tried = []

        class _Failing:
            def chat(self, system_prompt, user_prompt):
                raise RuntimeError("429 该模型当前访问量过大")

        class _Working:
            def chat(self, system_prompt, user_prompt):
                return '{"culture": "cn"}'

        def fake_build(name):
            tried.append(name)
            return _Failing() if name == "zhipu" else _Working()

        original = visual_setting._build_chat_provider
        visual_setting._build_chat_provider = fake_build
        try:
            chat = visual_setting._default_chat()
            self.assertEqual(chat("s", "u"), '{"culture": "cn"}')
        finally:
            visual_setting._build_chat_provider = original
        self.assertEqual(tried, ["zhipu", "gemini"])

    def test_unknown_provider_name_rejected(self):
        with self.assertRaises(RuntimeError):
            visual_setting._build_chat_provider("nope")


if __name__ == "__main__":
    unittest.main()
