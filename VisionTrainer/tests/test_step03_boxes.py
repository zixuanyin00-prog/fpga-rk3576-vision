from pathlib import Path
import copy
import hashlib
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import store as db, scene_boxes
from core.native_frames import finalize
from core.point_box import propose
from core.proposals import supplemental
from service import Service


class Step03Tests(unittest.TestCase):
    def test_edges_nested_duplicates_and_group_box(self):
        rows = [
            dict(bbox=[0, 50, 60, 100], score=0.7),
            dict(bbox=[2, 51, 58, 99], score=0.8),
            dict(bbox=[95, 50, 145, 100], score=0.8),
            dict(bbox=[0, 40, 160, 110], score=0.99),
        ]
        result = finalize(rows, (512, 288))
        self.assertEqual(len(result), 2)
        self.assertTrue(any(o["edge_review"] for o in result))
        self.assertFalse(any(o["bbox"] == [0, 40, 160, 110] for o in result))

    def test_close_components_are_not_deduplicated(self):
        rows = [dict(bbox=[30, 30, 70, 60]), dict(bbox=[65, 30, 105, 60])]
        self.assertEqual(len(finalize(rows, (512, 288))), 2)

    def test_point_on_native_pixels_and_uncertain_blank(self):
        pixels = np.full((345, 709, 3), 220, np.uint8)
        pixels[120:180, 300:390] = [35, 45, 50]
        found = propose(Image.fromarray(pixels), [345 * 512 / 709, 150 * 288 / 345])
        self.assertFalse(found["uncertain"])
        self.assertTrue(found["needs_review"])
        np.testing.assert_allclose(
            found["proposal_bbox_native"], [298, 118, 392, 182], atol=2
        )
        blank = propose(Image.new("RGB", (709, 345), "white"), [256, 144])
        self.assertTrue(blank["uncertain"])
        self.assertLess(blank["bbox"][2] - blank["bbox"][0], 150)

    def test_preserves_exact_human_objects(self):
        human = [
            dict(bbox=[0, 30, 40, 60], label=-1, reviewer="user"),
            dict(bbox=[80, 30, 120, 60], label=2),
        ]
        result, n = supplemental(
            human,
            [
                dict(bbox=[1, 31, 39, 59]),
                dict(bbox=[160, 30, 200, 60], edge_review=True),
            ],
        )
        self.assertEqual(result[:2], human)
        self.assertEqual(n, 1)
        self.assertTrue(result[-1]["edge_review"])

    def fixture(self, root):
        p = db.create("scene")
        rows = []
        for i in range(5):
            raw = bytes([i]) * 512 * 288 * 3
            name = str(i) + ".rgb"
            (db.project_path(p["id"]) / name).write_bytes(raw)
            rows.append(
                dict(
                    id=str(i),
                    batch="batch",
                    role="train",
                    file=name,
                    sha256=hashlib.sha256(raw).hexdigest(),
                    native_roi=True,
                    reviewed=True,
                    all_objects_reviewed=True,
                    quality_review="pass",
                    origin=[0, 0],
                    objects=[
                        dict(bbox=[20, 30, 70, 70], label=0),
                        dict(bbox=[120, 30, 170, 70], label=1),
                    ],
                )
            )
        p["frames"] = rows
        db.save(p)
        return p

    def test_scene_training_excludes_unconfirmed_test_and_other_batch(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(db, "DATA", Path(tmp)):
            p = self.fixture(tmp)
            p["frames"] += [
                dict(copy.deepcopy(p["frames"][0]), id="unconfirmed", reviewed=False),
                dict(copy.deepcopy(p["frames"][1]), id="test", role="test"),
                dict(copy.deepcopy(p["frames"][2]), id="other", batch="other"),
            ]
            _, rows = scene_boxes.snapshot(p, "batch")
            self.assertEqual([f["id"] for f in rows], list("01234"))
            p["frames"][0]["objects"] = []
            p["frames"][1]["objects"] = []
            p["frames"][2]["objects"] = []
            with self.assertRaisesRegex(ValueError, "6个"):
                scene_boxes.snapshot(p, "batch")

    def test_confirmed_frame_cannot_be_overwritten_by_assist(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(db, "DATA", Path(tmp)):
            p = self.fixture(tmp)
            original = (db.project_path(p["id"]) / "project.json").read_bytes()
            s = Service()
            s.job = {}
            with patch.object(
                s, "run_suggestions", side_effect=AssertionError("must not infer")
            ):
                with self.assertRaisesRegex(ValueError, "已确认"):
                    s.assist_action(p["id"], "0")
            self.assertEqual(
                original, (db.project_path(p["id"]) / "project.json").read_bytes()
            )

    def test_rejected_false_box_not_added_again(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(db, "DATA", Path(tmp)):
            p = self.fixture(tmp)
            f = p["frames"][0]
            f.update(
                reviewed=False,
                objects=[],
                rejected_boxes=[[20, 30, 70, 70]],
                annotated_at="saved",
            )
            db.save(p)
            s = Service()
            s.job = {}
            with patch.object(
                s,
                "run_suggestions",
                return_value={"0": [dict(bbox=[21, 31, 69, 69], label=-2)]},
            ):
                s.assist_action(p["id"], "0")
            self.assertEqual(db.load(p["id"])["frames"][0]["objects"], [])

    def test_native_tiles_keep_pixel_scale_and_ignore_seam_fragments(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = (
                np.random.default_rng(1)
                .integers(0, 255, (345, 709, 3), dtype=np.uint8)
                .tobytes()
            )
            (root / "x.rgb").write_bytes(raw)
            f = dict(
                original_file="x.rgb",
                original_size=[709, 345],
                original_sha256=hashlib.sha256(raw).hexdigest(),
                objects=[
                    dict(
                        bbox=[
                            300 * 512 / 709,
                            120 * 288 / 345,
                            400 * 512 / 709,
                            200 * 288 / 345,
                        ],
                        label=0,
                    )
                ],
            )
            data, _ = scene_boxes.native_training_data(root, [f])
            np.testing.assert_array_equal(
                data[0][0],
                np.frombuffer(raw, np.uint8).reshape(345, 709, 3)[:288, :512],
            )
            np.testing.assert_allclose(data[0][1][0]["bbox"], [300, 120, 400, 200])

    def test_refill_replaces_only_unconfirmed_machine_boxes(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(db, "DATA", Path(tmp)):
            p = self.fixture(tmp)
            f = p["frames"][0]
            human = copy.deepcopy(f["objects"][0])
            stale = dict(
                bbox=[300, 40, 340, 70], label=-2, proposed=True, needs_review=True
            )
            f.update(reviewed=False, objects=[human, stale], annotated_at="saved")
            db.save(p)
            s = Service()
            s.job = {}
            with patch.object(
                s,
                "run_suggestions",
                return_value={"0": [dict(bbox=[120, 40, 160, 70], label=-2)]},
            ):
                s.assist_action(p["id"], "0")
            found = db.load(p["id"])["frames"][0]["objects"]
            self.assertEqual(found[0]["bbox"], human["bbox"])
            self.assertEqual(found[0]["label"], human["label"])
            self.assertFalse(any(o["bbox"] == stale["bbox"] for o in found))


if __name__ == "__main__":
    unittest.main()
