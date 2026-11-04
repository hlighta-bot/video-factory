"""Zhipu (GLM) Story Provider unit tests.

Purpose:
- Verify the "zhipu" route builds a provider with sane defaults.
- Verify env precedence for ZHIPU_* keys without touching the project .env.
- Verify the provider reuses the shared OpenAI-compatible wire format.
- Keep every assertion offline: no network calls are made.

Input data:
- None (environment variables are injected and restored per test).

Output data:
- Assertion-only test module, compatible with pytest and `python tests/<file>`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from story.openai_provider import SYSTEM_PROMPT
from story.router import create_story_provider
from story.zhipu_provider import (
    DEFAULT_ZHIPU_BASE_URL,
    DEFAULT_ZHIPU_TEXT_MODEL,
    ZhipuChatProvider,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ZHIPU_ENV_KEYS = (
    "ZHIPU_API_KEY",
    "ZHIPU_BASE_URL",
    "ZHIPU_TEXT_MODEL",
    "ZHIPU_TIMEOUT",
    "ZHIPU_RETRIES",
)


def _patched_env(values: dict[str, str]):
    """Context manager that swaps ZHIPU_* env vars and restores them after."""

    class _Ctx:
        def __enter__(self):
            self._saved = {key: os.environ.get(key) for key in ZHIPU_ENV_KEYS}
            for key in ZHIPU_ENV_KEYS:
                os.environ.pop(key, None)
            os.environ.update(values)
            return self

        def __exit__(self, *exc_info):
            for key in ZHIPU_ENV_KEYS:
                value = self._saved[key]
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            return False

    return _Ctx()


def test_router_builds_zhipu_provider():
    with _patched_env({"ZHIPU_API_KEY": "unit-key"}):
        provider = create_story_provider("zhipu")
    assert isinstance(provider, ZhipuChatProvider)
    assert provider.name == "zhipu"
    assert provider.base_url == DEFAULT_ZHIPU_BASE_URL
    assert provider.model == DEFAULT_ZHIPU_TEXT_MODEL
    assert provider.request_url == f"{DEFAULT_ZHIPU_BASE_URL}/chat/completions"


def test_env_overrides_defaults():
    with _patched_env(
        {
            "ZHIPU_API_KEY": "unit-key",
            "ZHIPU_BASE_URL": "https://example.invalid/api/paas/v4/",
            "ZHIPU_TEXT_MODEL": "glm-test-flash",
            "ZHIPU_TIMEOUT": "42",
            "ZHIPU_RETRIES": "1",
        }
    ):
        provider = create_story_provider("zhipu")
    assert provider.base_url == "https://example.invalid/api/paas/v4"
    assert provider.model == "glm-test-flash"
    assert provider.timeout == 42.0
    # request_url must not double up on slashes after the trailing-slash input
    assert provider.request_url == "https://example.invalid/api/paas/v4/chat/completions"


def test_explicit_arguments_win():
    provider = create_story_provider(
        "zhipu",
        api_key="explicit-key",
        base_url="https://explicit.invalid/v4",
        model="glm-explicit",
        timeout=5,
        retries=0,
        load_env=False,
    )
    assert provider._api_key == "explicit-key"
    assert provider.base_url == "https://explicit.invalid/v4"
    assert provider.model == "glm-explicit"
    assert provider.timeout == 5.0


def test_chat_uses_openai_wire_format(monkeypatch):
    provider = create_story_provider(
        "zhipu", api_key="unit-key", model="glm-test-flash", load_env=False
    )
    captured = {}

    def fake_request(system_prompt, user_prompt, temperature=0.0):
        captured["system_prompt"] = system_prompt
        captured["user_prompt"] = user_prompt
        captured["temperature"] = temperature
        return json.dumps({"choices": [{"message": {"content": "{\"version\": \"1.0\"}"}}]})

    monkeypatch.setattr(provider, "_request", fake_request)
    out = provider.chat(SYSTEM_PROMPT, "small topic", temperature=0.0)
    assert json.loads(out)["version"] == "1.0"
    assert captured["system_prompt"] == SYSTEM_PROMPT
    assert captured["user_prompt"] == "small topic"


def test_missing_key_raises():
    with _patched_env({}):
        # load_env=False keeps the project .env out of the picture so this test
        # stays hermetic even when a real ZHIPU_API_KEY is configured.
        provider = create_story_provider("zhipu", load_env=False)
    assert provider._api_key == ""
    try:
        provider._request("s", "u")
    except RuntimeError as exc:
        assert "ZHIPU_API_KEY" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("expected RuntimeError when ZHIPU_API_KEY is missing")


def main() -> int:
    class _Monkey:
        def __init__(self):
            self._undo = []

        def setattr(self, obj, name, value):
            self._undo.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)

        def undo(self):
            for obj, name, value in reversed(self._undo):
                setattr(obj, name, value)

    cases = [
        test_router_builds_zhipu_provider,
        test_env_overrides_defaults,
        test_explicit_arguments_win,
    ]
    for case in cases:
        case()

    monkey = _Monkey()
    test_chat_uses_openai_wire_format(monkey)
    monkey.undo()

    test_missing_key_raises()
    print(f"OK: {len(cases) + 2} zhipu provider checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
