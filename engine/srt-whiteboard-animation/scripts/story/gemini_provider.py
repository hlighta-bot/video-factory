"""Gemini Story Provider backed by the official google-genai SDK."""

from __future__ import annotations

import os
import time

from .base import StoryProvider
from .config import load_project_env
from .openai_provider import SYSTEM_PROMPT


DEFAULT_MODEL = "gemini-3.1-flash-lite"
DEFAULT_FALLBACK_MODELS = "gemini-3.5-flash-lite,gemini-2.5-flash"
DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_OUTPUT_TOKENS = 1024
DEFAULT_TIMEOUT = 300.0
DEFAULT_RETRIES = 3
# Substrings marking a transient upstream condition worth retrying / failing over.
RETRYABLE_MARKERS = (
    "503",
    "429",
    "500",
    "502",
    "504",
    "UNAVAILABLE",
    "RESOURCE_EXHAUSTED",
    "INTERNAL",
    "DEADLINE_EXCEEDED",
    "HIGH DEMAND",
    "TIMEOUT",
    "TIMED OUT",
    "TEMPORARILY",
)


class GeminiStoryProvider(StoryProvider):
    """Story provider that returns raw Gemini response text.

    Model chain: ``GEMINI_MODEL`` first, then ``GEMINI_MODEL_FALLBACKS``
    (comma separated). When every retry of a model fails with a transient
    upstream error (503/429/...), the next model in the chain is tried so a
    single overloaded endpoint cannot break the whole pipeline.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        fallback_models: str | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        timeout: float | str | None = None,
        retries: int | str | None = None,
        client: object | None = None,
        load_env: bool = True,
    ):
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise RuntimeError(
                "Gemini provider requires google-genai. Run "
                "python scripts/prepare_env.py --provider gemini"
            ) from exc

        if load_env:
            load_project_env()
        self._api_key = api_key if api_key is not None else os.getenv("GEMINI_API_KEY", "")
        primary = model if model is not None else os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
        raw_fallbacks = (
            fallback_models
            if fallback_models is not None
            else os.getenv("GEMINI_MODEL_FALLBACKS", DEFAULT_FALLBACK_MODELS)
        )
        chain = [primary.strip()] if primary and primary.strip() else [DEFAULT_MODEL]
        for item in str(raw_fallbacks or "").split(","):
            name = item.strip()
            if name and name not in chain:
                chain.append(name)
        self._models = chain
        self._temperature = DEFAULT_TEMPERATURE if temperature is None else temperature
        self._max_output_tokens = (
            DEFAULT_MAX_OUTPUT_TOKENS if max_output_tokens is None else max_output_tokens
        )
        self._timeout = _parse_timeout(
            timeout if timeout is not None else os.getenv("GEMINI_TIMEOUT", DEFAULT_TIMEOUT)
        )
        self._retries = _parse_retries(
            retries if retries is not None else os.getenv("GEMINI_RETRIES", DEFAULT_RETRIES)
        )
        self._types = types
        if client is not None:
            self._client = client
        else:
            # google-genai expects http_options.timeout in milliseconds.
            http_options = types.HttpOptions(timeout=int(self._timeout * 1000))
            self._client = genai.Client(
                api_key=self._api_key or None,
                http_options=http_options,
            )

    @property
    def name(self) -> str:
        return "gemini"

    @property
    def model(self) -> str:
        return self._models[0]

    @property
    def models(self) -> list[str]:
        return list(self._models)

    @property
    def temperature(self) -> float:
        return self._temperature

    @property
    def max_output_tokens(self) -> int:
        return self._max_output_tokens

    @property
    def timeout(self) -> float:
        return self._timeout

    @property
    def retries(self) -> int:
        return self._retries

    def generate(self, topic: str) -> str:
        return self._call(topic)

    def chat(self, system_prompt: str, user_prompt: str, temperature: float = 0.0) -> str:
        """Expose an OpenAI-wire-compatible surface for callers such as visual_setting."""

        return self._call(user_prompt, system_prompt=system_prompt, temperature=temperature)

    def _call(
        self,
        contents: str,
        *,
        system_prompt: str | None = None,
        temperature: float | None = None,
    ) -> str:
        if not self._api_key:
            raise RuntimeError("GEMINI_API_KEY is required for gemini story provider")

        request = self._types.GenerateContentConfig(
            system_instruction=system_prompt if system_prompt is not None else SYSTEM_PROMPT,
            temperature=self._temperature if temperature is None else temperature,
            max_output_tokens=self._max_output_tokens,
        )

        last_error: Exception | None = None
        for model_index, model_name in enumerate(self._models):
            for attempt in range(self._retries + 1):
                try:
                    response = self._client.models.generate_content(
                        model=model_name,
                        contents=contents,
                        config=request,
                    )
                except Exception as exc:  # noqa: BLE001 - classify, then retry/fail over
                    last_error = exc
                    if not _is_retryable(exc):
                        raise RuntimeError(f"gemini provider error: {exc}") from exc
                    if attempt < self._retries:
                        delay = min(2 ** attempt, 8)
                        print(
                            f"[warn] gemini({model_name}) transient failure; retrying in "
                            f"{delay}s ({attempt + 1}/{self._retries}): {exc}"
                        )
                        time.sleep(delay)
                        continue
                    break  # exhausted retries for this model -> try next model

                text = getattr(response, "text", None)
                if text:
                    return text
                last_error = RuntimeError(
                    f"gemini({model_name}) returned empty response"
                )
                if attempt >= self._retries:
                    break
                delay = min(2 ** attempt, 8)
                print(f"[warn] gemini({model_name}) empty response; retrying in {delay}s")
                time.sleep(delay)

            if model_index + 1 < len(self._models):
                nxt = self._models[model_index + 1]
                print(
                    f"[warn] gemini model {model_name} unavailable after "
                    f"{self._retries + 1} attempts; falling back to {nxt}"
                )

        raise RuntimeError(f"gemini provider error: {last_error}")


def _is_retryable(exc: Exception) -> bool:
    message = f"{type(exc).__name__}: {exc}".upper()
    return any(marker in message for marker in RETRYABLE_MARKERS)


def _parse_timeout(value: float | str | int) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"GEMINI_TIMEOUT must be a number, got {value!r}") from exc
    if timeout <= 0:
        raise ValueError("GEMINI_TIMEOUT must be positive")
    return timeout


def _parse_retries(value: int | str) -> int:
    try:
        retries = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"GEMINI_RETRIES must be an integer, got {value!r}") from exc
    if not 0 <= retries <= 5:
        raise ValueError("GEMINI_RETRIES must be an integer in [0, 5]")
    return retries
