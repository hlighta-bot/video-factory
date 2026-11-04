import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from image_generation import default_image_provider  # noqa: E402


class DefaultImageProviderTest(unittest.TestCase):
    """CLI default must follow $IMAGE_PROVIDER and degrade safely."""

    def setUp(self):
        self._saved = os.environ.get("IMAGE_PROVIDER")

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("IMAGE_PROVIDER", None)
        else:
            os.environ["IMAGE_PROVIDER"] = self._saved

    def test_reads_env_value(self):
        os.environ["IMAGE_PROVIDER"] = "zhipu"
        self.assertEqual(default_image_provider(), "zhipu")

    def test_case_and_space_insensitive(self):
        os.environ["IMAGE_PROVIDER"] = "  Zhipu "
        self.assertEqual(default_image_provider(), "zhipu")

    def test_auto_passthrough(self):
        os.environ["IMAGE_PROVIDER"] = "auto"
        self.assertEqual(default_image_provider(), "auto")

    def test_none_passthrough(self):
        os.environ["IMAGE_PROVIDER"] = "none"
        self.assertEqual(default_image_provider(), "none")

    def test_unknown_value_falls_back_to_none(self):
        os.environ["IMAGE_PROVIDER"] = "dalle"
        self.assertEqual(default_image_provider(), "none")


if __name__ == "__main__":
    unittest.main()
