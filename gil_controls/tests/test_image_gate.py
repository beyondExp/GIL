import base64
import io
import os
import sys
import unittest

import pytest

pytestmark = pytest.mark.phase1


SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from image_gate import image_gate  # noqa: E402


def _jpeg_data_url(gray_value: int, *, w: int = 160, h: int = 90) -> str:
    from PIL import Image

    im = Image.new("L", (w, h), color=int(gray_value))
    buf = io.BytesIO()
    im.convert("RGB").save(buf, format="JPEG", quality=80)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return "data:image/jpeg;base64," + b64


def _noise_data_url(*, w: int = 160, h: int = 90) -> str:
    from PIL import Image
    import numpy as np

    arr = np.random.randint(0, 255, size=(h, w, 3), dtype="uint8")
    im = Image.fromarray(arr, mode="RGB")
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=80)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return "data:image/jpeg;base64," + b64


class ImageGateTests(unittest.TestCase):
    def test_rejects_missing(self):
        rep = image_gate(None)
        self.assertFalse(rep.ok)

    def test_rejects_black(self):
        rep = image_gate(_jpeg_data_url(0))
        self.assertFalse(rep.ok)

    def test_rejects_white(self):
        rep = image_gate(_jpeg_data_url(255))
        self.assertFalse(rep.ok)

    def test_accepts_noise(self):
        rep = image_gate(_noise_data_url(), min_std=3.0)
        self.assertTrue(rep.ok)


if __name__ == "__main__":
    unittest.main()

