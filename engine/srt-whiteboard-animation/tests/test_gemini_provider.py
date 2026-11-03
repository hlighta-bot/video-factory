"""Gemini Story Provider unit tests.

Purpose:
- Verify Gemini provider construction without crashing when GEMINI_API_KEY is absent.
- Verify router wiring for "gemini".
- Verify env/config precedence and mocked client flow.
- Keep normal unit tests offline.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import story.config as story_config
from story.gemini_provider import GeminiStoryProvider
from story.openai_provider import SYSTEM_PROMPT
from story.router import create_story_provider
from story_schema import validate_story


SCRIPT_START = time.time()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = PROJECT_ROOT / "output" / "phase29.2" / "gemini-provider-report.json"


def _write_env(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "GEMINI_API_KEY=env-file-key",
                "GEMINI_MODEL=gemini-env-model",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _provider_with_env(env_path: Path, *, shell_key: str | None = None, shell_model: str | None = None):
    _write_env(env_path)
    saved = {key: os.environ.get(key) for key in ("GEMINI_API_KEY", "GEMINI_MODEL")}

    try:
        for key in ("GEMINI_API_KEY", "GEMINI_MODEL"):
            os.environ.pop(key, None)
        if shell_key is not None:
            os.environ["GEMINI_API_KEY"] = shell_key
        if shell_model is not None:
            os.environ["GEMINI_MODEL"] = shell_model
        return create_story_provider("gemini")
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class _FakeModels:
    def __init__(self, response_text: str):
        self._response_text = response_text
        self.last_model = None
        self.last_contents = None
        self.last_config = None

    def generate_content(self, *, model, contents, config):
        self.last_model = model
        self.last_contents = contents
        self.last_config = config
        return _FakeResponse(text=self._response_text)


class _FakeClient:
    def __init__(self, response_text: str):
        self.models = _FakeModels(response_text)


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text


def main() -> int:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    assert story_config.project_root() == PROJECT_ROOT
    assert import_ok_without_api_key()

    env_provider = _provider_with_env(PROJECT_ROOT / ".env")
    assert isinstance(env_provider, GeminiStoryProvider)
    assert env_provider.name == "gemini"
    assert env_provider.model == "gemini-env-model"
    assert env_provider._api_key == "env-file-key"

    shell_provider = _provider_with_env(PROJECT_ROOT / ".env", shell_key="shell-key", shell_model="shell-model")
    assert isinstance(shell_provider, GeminiStoryProvider)
    assert shell_provider._api_key == "shell-key"
    assert shell_provider.model == "shell-model"

    no_key_saved = {key: os.environ.get(key) for key in ("GEMINI_API_KEY", "GEMINI_MODEL")}
    try:
        for key in ("GEMINI_API_KEY", "GEMINI_MODEL"):
            os.environ.pop(key, None)
        no_key_provider = GeminiStoryProvider(api_key=None, client=_FakeClient("{}"), load_env=False)
        try:
            no_key_provider.generate("any topic")
        except RuntimeError as exc:
            assert "GEMINI_API_KEY is required" in str(exc)
        else:
            raise AssertionError("missing API key was accepted")
    finally:
        for key, value in no_key_saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    story_json = json.dumps(
        {
            "version": "1.0",
            "title": "Rocket Launch",
            "segments": [
                {"text": "火箭停在发射台上。"},
                {"text": "火箭点火。"},
                {"text": "火箭升空。"},
            ],
        },
        ensure_ascii=False,
    )
    fake_client = _FakeClient(story_json)
    provider = GeminiStoryProvider(api_key="test-key", model="gemini-test-model", client=fake_client, load_env=False)
    raw = provider.generate("介绍中国的万里长城")
    assert raw == story_json
    assert fake_client.models.last_model == "gemini-test-model"
    assert fake_client.models.last_contents == "介绍中国的万里长城"
    assert fake_client.models.last_config.system_instruction == SYSTEM_PROMPT
    assert fake_client.models.last_config.temperature == 0.0
    assert fake_client.models.last_config.max_output_tokens == 1024
    validate_story(json.loads(raw))

    router_provider = create_story_provider("gemini", api_key="test-key", model="gemini-router-model", client=_FakeClient("{}"), load_env=False)
    assert router_provider.name == "gemini"
    assert router_provider.model == "gemini-router-model"

    report = {
        "phase": 29.2,
        "status": "pass",
        "provider": "gemini",
        "router_ok": True,
        "no_key_import_ok": True,
        "env_loaded": True,
        "shell_over_env": True,
        "mocked_generate_ok": True,
        "elapsed_sec": round(time.time() - SCRIPT_START, 3),
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"elapsed_sec={round(time.time() - SCRIPT_START, 3):.3f}")
    return 0


def import_ok_without_api_key() -> bool:
    saved = {key: os.environ.get(key) for key in ("GEMINI_API_KEY", "GEMINI_MODEL")}
    try:
        for key in saved:
            os.environ.pop(key, None)
        from story.gemini_provider import GeminiStoryProvider as _Imported  # noqa: F401

        return True
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    raise SystemExit(main())
