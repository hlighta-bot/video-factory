"""SenseNova U1.5 Lite image generation over its OpenAI-compatible endpoint."""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from story.config import load_project_env


DEFAULT_BASE_URL = "https://token.sensenova.cn/v1"
DEFAULT_MODEL = "sensenova-u1.5-lite"
DEFAULT_TIMEOUT = 300.0
DEFAULT_RETRIES = 2
DEFAULT_SIZE = "1024x576"


class SenseNovaImageProvider:
    """Generate and persist one PNG for a scene.

    Configuration accepts dedicated SenseNova variables and falls back to the
    project's existing OpenAI-compatible settings, so existing SenseNova
    credentials in ``OPENAI_API_KEY`` work without copying secrets.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        retries: int | None = None,
        load_env: bool = True,
    ):
        if load_env:
            load_project_env()
        dedicated_key = api_key or os.getenv("SENSENOVA_API_KEY")
        shared_key = os.getenv("OPENAI_API_KEY", "")
        shared_base_url = os.getenv("OPENAI_BASE_URL", "")
        self.api_key = dedicated_key or shared_key
        if base_url or os.getenv("SENSENOVA_BASE_URL"):
            resolved_base_url = base_url or os.getenv("SENSENOVA_BASE_URL", "")
        elif dedicated_key:
            resolved_base_url = DEFAULT_BASE_URL
        elif "sensenova" in shared_base_url.lower():
            resolved_base_url = shared_base_url
        elif shared_key:
            raise RuntimeError(
                "The shared OPENAI_API_KEY is not paired with a SenseNova base URL; "
                "set SENSENOVA_API_KEY or SENSENOVA_BASE_URL explicitly"
            )
        else:
            resolved_base_url = DEFAULT_BASE_URL
        self.base_url = resolved_base_url.rstrip("/")
        self.model = model or os.getenv("SENSENOVA_IMAGE_MODEL", DEFAULT_MODEL)
        self.timeout = float(timeout if timeout is not None else os.getenv("SENSENOVA_IMAGE_TIMEOUT", DEFAULT_TIMEOUT))
        self.retries = int(retries if retries is not None else os.getenv("SENSENOVA_IMAGE_RETRIES", DEFAULT_RETRIES))
        if self.timeout <= 0:
            raise ValueError("SENSENOVA_IMAGE_TIMEOUT must be positive")
        if not 0 <= self.retries <= 5:
            raise ValueError("SENSENOVA_IMAGE_RETRIES must be an integer in [0, 5]")

    def generate(
        self,
        prompt: str,
        output_path: str | Path,
        *,
        size: str = DEFAULT_SIZE,
    ) -> Path:
        if not self.api_key:
            raise RuntimeError(
                "SenseNova image generation requires SENSENOVA_API_KEY or "
                "OPENAI_API_KEY in the project .env"
            )
        if not prompt.strip():
            raise ValueError("Image prompt must not be empty")
        _validate_size(size)

        payload = {
            "model": self.model,
            "prompt": prompt,
            "size": size,
            "n": 1,
            "output_format": "png",
            "response_format": "b64_json",
            "watermark": False,
            "prompt_extend": True,
        }
        request = urllib.request.Request(
            f"{self.base_url}/images/generations",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        for attempt in range(self.retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    result = json.loads(response.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if exc.code not in {429, 500, 502, 503, 504} or attempt >= self.retries:
                    raise RuntimeError(
                        f"SenseNova image API HTTP {exc.code}: {detail[:1000]}"
                    ) from exc
                last_error = exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt >= self.retries:
                    raise RuntimeError(
                        f"SenseNova image API timed out or failed after {attempt + 1} "
                        f"attempt(s) (timeout={self.timeout:g}s): {exc}"
                    ) from exc
                last_error = exc
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise RuntimeError("SenseNova image API returned invalid JSON") from exc
            delay = min(2 ** attempt, 8)
            print(
                f"[warn] SenseNova image request failed; retrying in {delay}s "
                f"({attempt + 1}/{self.retries}): {last_error}"
            )
            time.sleep(delay)
        data = result.get("data") or []
        encoded = data[0].get("b64_json") if data and isinstance(data[0], dict) else None
        if not encoded:
            raise RuntimeError(
                "SenseNova image API response did not contain data[0].b64_json"
            )
        try:
            image_bytes = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise RuntimeError("SenseNova image API returned invalid base64 image data") from exc

        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".part")
        temporary.write_bytes(image_bytes)
        temporary.replace(target)
        _validate_png(target)
        return target


def _validate_size(size: str) -> None:
    try:
        width, height = (int(part) for part in size.lower().split("x", 1))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid image size {size!r}; expected WIDTHxHEIGHT") from exc
    if not (512 <= width <= 4096 and 512 <= height <= 4096):
        raise ValueError("SenseNova image width and height must be in [512, 4096]")
    if width % 32 or height % 32:
        raise ValueError("SenseNova image width and height must be multiples of 32")
    if max(width, height) / min(width, height) > 3:
        raise ValueError("SenseNova image aspect ratio must be within 3:1")


def _validate_png(path: Path) -> None:
    try:
        from PIL import Image

        with Image.open(path) as image:
            if image.format != "PNG":
                raise ValueError(f"SenseNova returned {image.format or 'unknown'} instead of PNG")
            image.verify()
    except Exception as exc:
        path.unlink(missing_ok=True)
        if isinstance(exc, ValueError):
            raise
        raise RuntimeError(f"SenseNova returned an unreadable PNG: {exc}") from exc


def build_scene_image_prompt(scene: Any) -> str:
    """Translate scene fields into a stable, caption-free illustration prompt."""
    visual = scene.visual or {}
    visual_plan = visual.get("visual_plan") or {}
    semantic = visual_plan.get("semantic") or visual.get("semantic") or {}
    subject = str(semantic.get("subject") or visual_plan.get("subject") or "")
    objects = semantic.get("objects") or []
    environment = str(semantic.get("environment") or visual_plan.get("environment") or "")
    action = str(semantic.get("action") or visual_plan.get("action") or "")
    narration = " ".join(str(scene.narration or "").split())

    scene_details = narration or " ".join(
        str(item.get("text", "")) for item in (scene.subtitles or [])
    )
    facts = []
    if subject and subject != "scene":
        facts.append(f"main subject: {subject}")
    if objects:
        facts.append("other visible subjects or objects: " + ", ".join(map(str, objects[:4])))
    if environment and environment not in {"story", "scene"}:
        facts.append(f"setting: {environment}")
    if action and action not in {"show", "story"}:
        facts.append(f"visible action: {action}")

    description = scene_details or ", ".join(facts)
    semantic_hint = "; ".join(facts)
    return (
        "Create one beautiful wide 16:9 illustration for a narrated video scene. "
        "Use a polished storybook editorial illustration style with painterly texture, "
        "layered depth, deliberate composition, rich but natural colors, and soft cinematic lighting. "
        f"Depict this scene faithfully: {description}. "
        f"Visual guidance: {semantic_hint or 'clear central subject and readable environment'}. "
        "Do not include any text, letters, captions, subtitles, labels, borders, logos, or watermarks."
    )
