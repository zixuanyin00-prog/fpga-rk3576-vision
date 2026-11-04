from pathlib import Path
import tempfile
import unittest
import sys
import hashlib
import json
import numpy as np
import torch
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import store as db
from core.data import augment, targets
from worker import load_net, evaluate


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original = db.DATA
        db.DATA = Path(self.temp.name)

    def tearDown(self):
        db.DATA = self.original
        self.temp.cleanup()

    def test_unreviewed_and_missing_classes_block_training(self):
        p = db.create("test")
        p["frames"] = [dict(id="f", role="train", objects=[], reviewed=False)]
        db.save(p)
        self.assertFalse(db.training_ready(p)[0])
        db.annotate(p, "f", [], True)
        self.assertFalse(db.training_ready(p)[0])
        p["frames"][0]["objects"] = [
            dict(label=i, bbox=[10, 10, 30, 30]) for i in range(3) for _ in range(10)
        ]
        self.assertFalse(db.training_ready(p)[0])
        self.assertIn("15张", db.training_ready(p)[1])

    def test_pending_label_can_be_draft_but_never_confirmed(self):
        p = db.create("test")
        p["frames"] = [dict(id="f", role="train")]
        db.save(p)
        objects = [dict(bbox=[10, 10, 80, 70], label=-2)]
        db.annotate(p, "f", objects, False)
        with self.assertRaises(ValueError):
            db.annotate(p, "f", objects, True)

    def test_multiple_resistors_keep_independent_labels_and_pending_review(self):
        p = db.create("three resistors")
        p["frames"] = [dict(id="f", role="train")]
        db.save(p)
        objects = [
            dict(bbox=[10 + i * 150, 100, 100 + i * 150, 160], label=i, proposed=i == 1)
            for i in range(3)
        ]
        db.annotate(p, "f", objects, False)
        p = db.load(p["id"])
        self.assertEqual([o["label"] for o in p["frames"][0]["objects"]], [0, 1, 2])
        self.assertTrue(p["frames"][0]["objects"][1]["needs_review"])
        with self.assertRaises(ValueError):
            db.annotate(p, "f", p["frames"][0]["objects"], True)
        p["frames"][0]["objects"][1].update(
            label=2,
            needs_review=False,
            proposed=False,
            class_id=p["schema"]["class_ids"][2],
        )
        db.annotate(p, "f", p["frames"][0]["objects"], True)
        self.assertEqual(
            [o["label"] for o in db.load(p["id"])["frames"][0]["objects"]], [0, 2, 2]
        )

    def test_duplicates_rejected_across_roles(self):
        p = db.create("test")
        db.add_frames(p, [dict(id="a", role="train", sha256="same")])
        with self.assertRaises(ValueError):
            db.add_frames(p, [dict(id="b", role="test", sha256="same")])
        self.assertEqual(len(db.load(p["id"])["frames"]), 1)

    def test_numeric_schema_does_not_invent_back_number(self):
        s = db.schema(["242", "1001", "242"])
        self.assertEqual(
            s["labels"],
            [
                "front_upright:242",
                "front_inverted:242",
                "front_upright:1001",
                "front_inverted:1001",
                "back",
                "unreadable",
            ],
        )

    def test_adjacent_images_from_same_import_group_cannot_cross_splits(self):
        p = db.create("test")
        db.add_frames(
            p, [dict(id="a", role="train", sha256="a", source_group="captureA")]
        )
        with self.assertRaises(ValueError):
            db.add_frames(
                p, [dict(id="b", role="test", sha256="b", source_group="captureA")]
            )

    def test_class_expansion_preserves_detector_filters(self):
        from core.model import ResistorNet

        old = db.schema([])["labels"]
        labels = db.schema(["242", "1001"])["labels"]
        weights = ResistorNet(states=len(old)).state_dict()
        fixture = Path(self.temp.name) / "synthetic.pt"
        torch.save(weights, fixture)
        net = load_net(fixture, labels, old)
        self.assertTrue(
            torch.equal(
                net.state_dict()["head.8.weight"][:5].cpu(),
                weights["head.8.weight"][:5],
            )
        )
        self.assertEqual(net.head[-1].out_channels, 11)

    def test_no_test_gradient_snapshot_and_integrity(self):
        p = db.create("test")
        r = db.project_path(p["id"])
        (r / "a.rgb").write_bytes(b"abc")
        f = dict(
            id="a",
            role="test",
            reviewed=True,
            file="a.rgb",
            sha256=hashlib.sha256(b"abc").hexdigest(),
            objects=[],
        )
        db.add_frames(p, [f])
        self.assertEqual(db.snapshot(p, "train"), [])
        (r / "a.rgb").write_bytes(b"bad")
        with self.assertRaises(ValueError):
            db.snapshot(p, "test")

    def test_number_label_rotation_keeps_identity(self):
        labels = db.schema(["242", "1001"])["labels"]
        im = np.zeros((288, 512, 3), np.uint8)
        _, t = augment(
            im,
            [dict(bbox=[200, 100, 300, 160], label=2)],
            np.random.default_rng(4),
            1,
            labels,
        )
        self.assertIn(int(t[1][0, 6]), [2, 3])

    def test_unreadable_truth_and_rejection_stay_in_denominator(self):
        root = Path(self.temp.name)
        raw = bytes(512 * 288 * 3)
        (root / "one.rgb").write_bytes(raw)
        objects = [
            dict(bbox=[10 + i * 100, 90, 70 + i * 100, 150], label=3 if i == 3 else 2)
            for i in range(4)
        ]
        frame = dict(
            id="f",
            file="one.rgb",
            sha256=hashlib.sha256(raw).hexdigest(),
            objects=objects,
        )
        predictions = [
            dict(bbox=objects[0]["bbox"], label="back"),
            dict(bbox=objects[1]["bbox"], label="uncertain"),
            dict(bbox=objects[3]["bbox"], label="unreadable"),
            dict(bbox=[430, 90, 490, 150], label="back"),
        ]
        with patch("worker.predictions", return_value=predictions):
            result = evaluate(
                torch.nn.Linear(1, 1),
                [frame],
                root,
                {"labels": ["front_upright", "front_inverted", "back", "unreadable"]},
            )
        self.assertEqual(
            (
                result["total"],
                result["correct"],
                result["missed"],
                result["rejected"],
                result["unscorable"],
                result["extra"],
            ),
            (4, 1, 1, 1, 1, 1),
        )


if __name__ == "__main__":
    unittest.main()
