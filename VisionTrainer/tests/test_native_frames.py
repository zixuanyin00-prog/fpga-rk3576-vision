from pathlib import Path
import hashlib
import sys
import tempfile
import unittest
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.native_frames import load_native, windows, checked_windows, finalize


class NativeFrames(unittest.TestCase):
    def fixture(self, root, size):
        width, height = size
        pixels = np.random.default_rng(17).integers(
            0, 256, (height, width, 3), dtype=np.uint8
        )
        raw = pixels.tobytes()
        (root / "native.rgb").write_bytes(raw)

        (root / "input.rgb").write_bytes(bytes(512 * 288 * 3))
        frame = dict(
            file="input.rgb",
            sha256=hashlib.sha256(bytes(512 * 288 * 3)).hexdigest(),
            original_file="native.rgb",
            original_size=[width, height],
            original_sha256=hashlib.sha256(raw).hexdigest(),
            origin=[0, 0],
            source_frame_id=123,
            model_input_is_native=False,
            native_roi=True,
        )
        return frame, pixels

    def test_native_pixels_not_resized_input(self):
        for size in [(621, 204), (709, 345), (1920, 1080), (80, 900), (64, 40)]:
            with self.subTest(size=size), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                frame, pixels = self.fixture(root, size)
                image, source = load_native(root, frame)
                self.assertEqual(image.size, size)
                np.testing.assert_array_equal(np.asarray(image), pixels)
                self.assertFalse(source["source_pixels_resized"])
                self.assertEqual(source["sha256"], frame["original_sha256"])
                cover = np.zeros((size[1], size[0]), dtype=bool)
                for x, y, x1, y1 in checked_windows(
                    windows(size, include_full=False), size
                ):
                    tile = image.crop((x, y, x1, y1))
                    np.testing.assert_array_equal(np.asarray(tile), pixels[y:y1, x:x1])
                    self.assertLessEqual(x1 - x, 512)
                    self.assertLessEqual(y1 - y, 288)
                    cover[y:y1, x:x1] = True
                self.assertTrue(cover.all())

    def test_missing_or_damaged_native_image_fails_without_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            frame, _ = self.fixture(root, (621, 204))
            (root / "native.rgb").write_bytes(bytes(621 * 204 * 3))
            with self.assertRaisesRegex(ValueError, "哈希"):
                load_native(root, frame)
            del frame["original_file"]
            with self.assertRaisesRegex(ValueError, "不能用缩放"):
                load_native(root, frame)

    def test_legacy_native_fixed_crop_is_allowed_but_unknown_input_is_not(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            raw = bytes(512 * 288 * 3)
            (root / "old.rgb").write_bytes(raw)
            frame = dict(
                file="old.rgb", sha256=hashlib.sha256(raw).hexdigest(), native_roi=True
            )
            self.assertEqual(load_native(root, frame)[0].size, (512, 288))
            frame["native_roi"] = False
            with self.assertRaises(ValueError):
                load_native(root, frame)

    def test_boxes_are_mapped_once_and_round_trip_to_native_pixels(self):
        size = (1920, 1080)
        box = [900.0, 300.0, 1100.0, 500.0]
        found = finalize([dict(bbox=box, score=0.9), dict(bbox=box, score=0.8)], size)
        self.assertEqual(len(found), 1)
        row = found[0]
        self.assertEqual(row["proposal_bbox_native"], box)
        self.assertEqual(
            row["bbox"], [240.0, 80.0, 1100 * 512 / 1920, 500 * 288 / 1080]
        )
        back = [row["bbox"][i] * size[i % 2] / [512, 288][i % 2] for i in range(4)]
        np.testing.assert_allclose(back, box)
        edge = finalize([dict(bbox=[0, 0, 20, 20])], size)
        self.assertEqual(len(edge), 1)
        self.assertTrue(edge[0]["edge_review"])
        self.assertEqual(finalize([dict(bbox=[10, 10, 2000, 20])], size), [])
        with self.assertRaises(ValueError):
            checked_windows([(0, 0, 1921, 1080)], size)


if __name__ == "__main__":
    unittest.main()
