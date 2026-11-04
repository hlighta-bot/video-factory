"""Zhipu (BigModel) CogView-3-Flash image generation provider.

The endpoint mirrors OpenAI's ``/images/generations`` shape, so this provider is
kept structurally parallel to :mod:`image_generation.sensenova` and can be
swapped in per run without touching the renderer.

Configuration (project ``.env`` or shell environment):

- ``ZHIPU_API_KEY`` (required) -- BigModel open-platform key
- ``ZHIPU_IMAGE_MODEL`` (optional, default ``cogview-3-flash``)
- ``ZHIPU_BASE_URL`` (optional, default ``https://open.bigmodel.cn/api/paas/v4``)
- ``ZHIPU_IMAGE_TIMEOUT`` / ``ZHIPU_IMAGE_RETRIES``
- ``ZHIPU_IMAGE_SIZE`` (optional, default ``1440x720``; must be one of the
  documented CogView-3-Flash resolutions)
- ``ZHIPU_IMAGE_CROP`` (optional, default ``1280x720``) -- the widest CogView
  resolution is 2:1, so the image is center-cropped to 16:9 to match the rest of
  the pipeline. Set ``off`` to keep the raw model output.
"""

from __future__ import annotations

import base64
import io
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from story.config import load_project_env


DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_MODEL = "cogview-3-flash"
DEFAULT_TIMEOUT = 180.0
DEFAULT_RETRIES = 2
# CogView-3-Flash documented resolutions (docs.bigmodel.cn free model page).
SUPPORTED_SIZES = (
    "1024x1024",
    "768x1344",
    "864x1152",
    "1344x768",
    "1152x864",
    "1440x720",
    "720x1440",
)
DEFAULT_SIZE = "1440x720"
DEFAULT_CROP = "1280x720"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class ZhipuImageProvider:
    """Generate one PNG per scene through CogView-3-Flash."""

    name = "zhipu"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        retries: int | None = None,
        size: str | None = None,
        crop: str | None = None,
        load_env: bool = True,
    ):
        if load_env:
            load_project_env()
        self.api_key = api_key if api_key is not None else os.getenv("ZHIPU_API_KEY", "")
        self.base_url = (
            base_url or os.getenv("ZHIPU_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        self.model = model or os.getenv("ZHIPU_IMAGE_MODEL", DEFAULT_MODEL)
        self.timeout = float(
            timeout if timeout is not None else os.getenv("ZHIPU_IMAGE_TIMEOUT", DEFAULT_TIMEOUT)
        )
        self.retries = int(
            retries if retries is not None else os.getenv("ZHIPU_IMAGE_RETRIES", DEFAULT_RETRIES)
        )
        if self.timeout <= 0:
            raise ValueError("ZHIPU_IMAGE_TIMEOUT must be positive")
        if not 0 <= self.retries <= 5:
            raise ValueError("ZHIPU_IMAGE_RETRIES must be an integer in [0, 5]")

        requested_size = size or os.getenv("ZHIPU_IMAGE_SIZE", DEFAULT_SIZE)
        _validate_size(requested_size)
        self.image_size = requested_size

        raw_crop = crop if crop is not None else os.getenv("ZHIPU_IMAGE_CROP", DEFAULT_CROP)
        raw_crop = (raw_crop or "").strip()
        if raw_crop.lower() in {"off", "none", "0", "false", "no"}:
            self.crop_size: str | None = None
        else:
            _validate_size(raw_crop)
            self.crop_size = raw_crop

    # ------------------------------------------------------------------ public
    def generate(
        self,
        prompt: str,
        output_path: str | Path,
        *,
        size: str | None = None,
    ) -> Path:
        if not self.api_key:
            raise RuntimeError(
                "Zhipu image generation requires ZHIPU_API_KEY in the project .env"
            )
        if not prompt.strip():
            raise ValueError("Image prompt must not be empty")
        request_size = size or self.image_size
        _validate_size(request_size)

        payload = {
            "model": self.model,
            "prompt": prompt,
            "size": request_size,
            "n": 1,
        }
        result = self._post_with_retry(payload)
        image_bytes = self._extract_image_bytes(result)

        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".part")
        temporary.write_bytes(image_bytes)
        temporary.replace(target)
        _normalise_png(target, self.crop_size)
        return target

    # ----------------------------------------------------------------- internal
    def _post_with_retry(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            request = urllib.request.Request(
                f"{self.base_url}/images/generations",
                data=body,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if exc.code not in RETRYABLE_STATUS or attempt >= self.retries:
                    raise RuntimeError(
                        f"Zhipu image API HTTP {exc.code}: {detail[:1000]}"
                    ) from exc
                last_error = exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt >= self.retries:
                    raise RuntimeError(
                        f"Zhipu image API timed out or failed after {attempt + 1} "
                        f"attempt(s) (timeout={self.timeout:g}s): {exc}"
                    ) from exc
                last_error = exc
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise RuntimeError("Zhipu image API returned invalid JSON") from exc
            delay = min(2 ** attempt, 8)
            print(
                f"[warn] Zhipu image request failed; retrying in {delay}s "
                f"({attempt + 1}/{self.retries}): {last_error}"
            )
            time.sleep(delay)
        raise RuntimeError(f"Zhipu image API failed: {last_error}")

    def _extract_image_bytes(self, result: dict[str, Any]) -> bytes:
        error = result.get("error")
        if error:
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise RuntimeError(f"Zhipu image API error: {message}")
        data = result.get("data") or []
        if not data or not isinstance(data[0], dict):
            raise RuntimeError("Zhipu image API response did not contain data[0]")

        entry = data[0]
        encoded = entry.get("b64_json")
        if encoded:
            try:
                return base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError) as exc:
                raise RuntimeError("Zhipu image API returned invalid base64 image data") from exc

        url = entry.get("url")
        if not url:
            raise RuntimeError(
                "Zhipu image API response contained neither data[0].url nor data[0].b64_json"
            )
        return self._download(url)

    def _download(self, url: str) -> bytes:
        if url.startswith("data:"):
            _, _, encoded = url.partition(",")
            try:
                return base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError) as exc:
                raise RuntimeError("Zhipu image URL carried invalid base64 data") from exc
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"Failed to download Zhipu generated image: {exc}") from exc


def _validate_size(size: str) -> None:
    if not isinstance(size, str) or "x" not in size.lower():
        raise ValueError(f"Invalid image size {size!r}; expected WIDTHxHEIGHT")
    try:
        width, height = (int(part) for part in size.lower().split("x", 1))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid image size {size!r}; expected WIDTHxHEIGHT") from exc
    if width <= 0 or height <= 0:
        raise ValueError(f"Invalid image size {size!r}; width/height must be positive")


def _normalise_png(path: Path, crop_size: str | None) -> None:
    """Re-encode the downloaded image as PNG and optionally centre-crop it."""

    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - Pillow is a project dependency
        raise RuntimeError("Pillow is required to normalise Zhipu images") from exc

    try:
        with Image.open(path) as image:
            image.load()
            frame = image if image.mode in {"RGB", "RGBA"} else image.convert("RGB")
            if crop_size:
                frame = _centre_crop(frame, crop_size)
            buffer = io.BytesIO()
            frame.save(buffer, format="PNG")
    except Exception as exc:
        path.unlink(missing_ok=True)
        raise RuntimeError(f"Zhipu returned an unreadable image: {exc}") from exc

    path.write_bytes(buffer.getvalue())
    _validate_png(path)


def _centre_crop(image: Any, crop_size: str) -> Any:
    width, height = (int(part) for part in crop_size.lower().split("x", 1))
    target_ratio = width / height
    source_ratio = image.width / image.height
    if abs(source_ratio - target_ratio) < 1e-6:
        return image
    if source_ratio > target_ratio:
        new_width = int(round(image.height * target_ratio))
        left = (image.width - new_width) // 2
        box = (left, 0, left + new_width, image.height)
    else:
        new_height = int(round(image.width / target_ratio))
        top = (image.height - new_height) // 2
        box = (0, top, image.width, top + new_height)
    return image.crop(box)


def _validate_png(path: Path) -> None:
    try:
        from PIL import Image

        with Image.open(path) as image:
            if image.format != "PNG":
                raise ValueError(f"Zhipu returned {image.format or 'unknown'} instead of PNG")
            image.verify()
    except Exception as exc:
        path.unlink(missing_ok=True)
        if isinstance(exc, ValueError):
            raise
        raise RuntimeError(f"Zhipu returned an unreadable PNG: {exc}") from exc
