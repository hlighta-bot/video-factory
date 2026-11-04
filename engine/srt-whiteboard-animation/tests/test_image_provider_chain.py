"""Tests for the Zhipu provider and the image provider failover chain."""

from __future__ import annotations

import base64
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from image_generation import (  # noqa: E402
    DEFAULT_PROVIDER_CHAIN,
    ImageProviderChain,
    ZhipuImageProvider,
    create_image_provider,
)


def _png_bytes(width: int = 1440, height: int = 720, colour=(245, 235, 215)) -> bytes:
    from PIL import Image

    image = Image.new("RGB", (width, height), colour)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class ZhipuProviderTests(unittest.TestCase):
    def _provider(self, **kwargs) -> ZhipuImageProvider:
        kwargs.setdefault("api_key", "test-key")
        return ZhipuImageProvider(load_env=False, **kwargs)

    def test_defaults_match_cogview_contract(self):
        provider = self._provider()
        self.assertEqual(provider.model, "cogview-3-flash")
        self.assertEqual(provider.image_size, "1440x720")
        self.assertEqual(provider.crop_size, "1280x720")
        self.assertTrue(provider.base_url.endswith("/paas/v4"))

    def test_missing_key_is_rejected(self):
        provider = self._provider(api_key="")
        with self.assertRaises(RuntimeError):
            provider.generate("a whiteboard sketch", Path(tempfile.gettempdir()) / "x.png")

    def test_invalid_size_is_rejected(self):
        with self.assertRaises(ValueError):
            self._provider(size="wide")

    def test_generate_writes_16_9_png_from_b64(self):
        payload = {"data": [{"b64_json": base64.b64encode(_png_bytes()).decode()}]}
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "scene-01.png"
            with mock.patch(
                "urllib.request.urlopen",
                return_value=_FakeResponse(__import__("json").dumps(payload).encode()),
            ) as urlopen:
                path = self._provider().generate("a whiteboard sketch", target)
            self.assertEqual(path, target)
            from PIL import Image

            with Image.open(path) as image:
                self.assertEqual(image.format, "PNG")
                self.assertEqual((image.width, image.height), (1280, 720))
            request = urlopen.call_args[0][0]
            self.assertEqual(request.full_url, "https://open.bigmodel.cn/api/paas/v4/images/generations")
            self.assertEqual(request.get_header("Authorization"), "Bearer test-key")

    def test_generate_downloads_url_when_b64_absent(self):
        image_bytes = _png_bytes()

        def fake_urlopen(request, timeout=None):
            if hasattr(request, "full_url") and request.full_url.startswith("https://open"):
                body = __import__("json").dumps({"data": [{"url": "https://cdn.example/img.png"}]})
                return _FakeResponse(body.encode())
            return _FakeResponse(image_bytes)

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "scene-01.png"
            with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
                self._provider().generate("a whiteboard sketch", target)
            self.assertTrue(target.is_file())

    def test_api_error_payload_surfaces(self):
        body = {"error": {"code": "1001", "message": "invalid api key"}}
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "urllib.request.urlopen",
                return_value=_FakeResponse(__import__("json").dumps(body).encode()),
            ):
                with self.assertRaises(RuntimeError) as ctx:
                    self._provider().generate("a sketch", Path(tmp) / "s.png")
            self.assertIn("invalid api key", str(ctx.exception))


class _StubProvider:
    def __init__(self, name: str, *, fail_after: int | None = None):
        self.name = name
        self.model = f"{name}-model"
        self.image_size = "1440x720"
        self.calls: list[str] = []
        self._fail_after = fail_after

    def generate(self, prompt, output_path, **kwargs):
        if self._fail_after is not None and len(self.calls) >= self._fail_after:
            raise RuntimeError(f"{self.name} exploded")
        self.calls.append(prompt)
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_png_bytes(64, 36))
        return target


class ImageProviderChainTests(unittest.TestCase):
    def test_create_image_provider_names(self):
        with self.assertRaises(ValueError):
            create_image_provider("midjourney")
        self.assertEqual(type(create_image_provider("zhipu", load_env=False)).__name__, "ZhipuImageProvider")

    def test_default_chain_prefers_sensenova(self):
        self.assertEqual(tuple(DEFAULT_PROVIDER_CHAIN), ("sensenova", "zhipu"))

    def test_failover_before_first_success(self):
        good = _StubProvider("zhipu")
        created: list[str] = []

        def factory(name, **_kwargs):
            created.append(name)
            if name == "sensenova":
                raise RuntimeError("missing SENSENOVA_API_KEY")
            return good

        chain = ImageProviderChain(("sensenova", "zhipu"), factory=factory)
        with tempfile.TemporaryDirectory() as tmp:
            chain.generate("p1", Path(tmp) / "1.png")
            chain.generate("p2", Path(tmp) / "2.png")
        self.assertEqual(chain.active_name, "zhipu")
        self.assertEqual(created, ["sensenova", "zhipu"])
        self.assertEqual(len(good.calls), 2)
        self.assertEqual(chain.attempts[0]["result"].startswith("failed"), True)

    def test_locked_provider_never_switches_mid_film(self):
        first = _StubProvider("sensenova", fail_after=1)
        second = _StubProvider("zhipu")

        def factory(name, **_kwargs):
            return first if name == "sensenova" else second

        chain = ImageProviderChain(("sensenova", "zhipu"), factory=factory)
        with tempfile.TemporaryDirectory() as tmp:
            chain.generate("p1", Path(tmp) / "1.png")
            with self.assertRaises(RuntimeError):
                chain.generate("p2", Path(tmp) / "2.png")
        self.assertEqual(chain.active_name, "sensenova")
        self.assertEqual(second.calls, [])

    def test_all_candidates_failing_raises(self):
        def factory(name, **_kwargs):
            raise RuntimeError(f"{name} unavailable")

        chain = ImageProviderChain(("sensenova", "zhipu"), factory=factory)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError) as ctx:
                chain.generate("p1", Path(tmp) / "1.png")
            self.assertFalse((Path(tmp) / "1.png").exists())
        self.assertIn("sensenova", str(ctx.exception))
        self.assertIn("zhipu", str(ctx.exception))

    def test_report_exposes_attempts(self):
        stub = _StubProvider("zhipu")
        chain = ImageProviderChain(("zhipu",), factory=lambda name, **_: stub)
        with tempfile.TemporaryDirectory() as tmp:
            chain.generate("p1", Path(tmp) / "1.png")
        report = chain.report()
        self.assertEqual(report["provider"], "zhipu")
        self.assertEqual(report["model"], "zhipu-model")
        self.assertEqual(report["attempts"], [{"provider": "zhipu", "result": "ok"}])


if __name__ == "__main__":
    unittest.main()
