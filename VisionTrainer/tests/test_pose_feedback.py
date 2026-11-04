import copy
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import store as db, feedback
from core.data import targets
from core.pair_export import validate_job


class PoseFeedbackTests(unittest.TestCase):
    def test_initial_pose_count_is_explicit_and_preserves_existing_schema(self):
        old = copy.deepcopy(self.p["schema"])
        single = db.create("one observed pose", pose_count=1)
        self.assertEqual(single["schema"]["titles"], ["姿态1"])
        feedback.add_pose(single, "add")
        self.assertEqual(single["schema"]["titles"], ["姿态1", "姿态2"])
        self.assertEqual(db.load(self.p["id"])["schema"], old)
        for invalid in (0, 31, True, 1.5, "1"):
            with self.assertRaises(ValueError):
                db.create("invalid", pose_count=invalid)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = db.DATA
        db.DATA = Path(self.tmp.name)
        self.p = db.create("isolated scene")
        self.root = db.project_path(self.p["id"])

    def tearDown(self):
        db.DATA = self.previous
        self.tmp.cleanup()

    def frame(self, id, role="train", objects=None):
        raw = bytes([len(self.p["frames"]) + 1]) * (512 * 288 * 3)
        (self.root / (id + ".rgb")).write_bytes(raw)
        f = dict(
            id=id,
            batch="b",
            role=role,
            file=id + ".rgb",
            sha256=hashlib.sha256(raw).hexdigest(),
            objects=objects or [],
            reviewed=True,
            all_objects_reviewed=True,
        )
        self.p["frames"].append(f)
        db.save(self.p)
        return f

    def objects(self):
        return [
            dict(bbox=[10 + i * 100, 60, 70 + i * 100, 120], label=i) for i in range(3)
        ]

    def test_append_identity_and_legacy_snapshot(self):
        old = copy.deepcopy(self.p["schema"])
        a = feedback.add_pose(self.p, "op")
        b = feedback.add_pose(self.p, "op")
        self.assertEqual(a, b)
        self.assertEqual(self.p["schema"]["labels"][:3], old["labels"])
        self.assertEqual(old["titles"], ["姿态1", "姿态2", "姿态3"])
        self.p["schema"] = db.schema(["242"])
        legacy = copy.deepcopy(self.p["schema"])
        ids = feedback.class_ids(legacy)
        feedback.add_pose(self.p, "legacy")
        self.assertEqual(self.p["schema"]["labels"][:-1], legacy["labels"])
        self.assertEqual(self.p["schema"]["class_ids"][:-1], ids)

    def test_negative_not_whole_image_and_ignore_loss(self):
        obs = self.objects() + [
            dict(bbox=[400, 10, 460, 40], label=-3),
            dict(bbox=[400, 200, 512, 288], label=-1),
        ]
        f = self.frame("f")
        db.annotate(self.p, "f", obs, True)
        heat, rows = targets(f["objects"])
        self.assertEqual(len(rows), 3)
        self.assertEqual(heat[5, 105], 0)
        self.assertEqual(heat[55, 110], -1)
        self.assertEqual(sum(o["label"] >= 0 for o in f["objects"]), 3)

    def test_pending_and_conflicts_block(self):
        f = self.frame("f", objects=self.objects())
        test = self.frame(
            "t", "test", objects=[dict(bbox=[10, 10, 30, 30], label=0, proposed=True)]
        )
        feedback.transfer(self.p, "t")
        with self.assertRaises(ValueError):
            feedback.prepare(self.p, "b", self.root)
        with self.assertRaises(ValueError):
            db.annotate(
                self.p,
                "t",
                [
                    dict(bbox=[10, 10, 50, 50], label=0),
                    dict(bbox=[20, 20, 40, 40], label=-3),
                ],
                True,
            )
        self.assertTrue(test["feedback_pending"])

    def test_cumulative_less_than15_and_leakage(self):
        self.frame("a", objects=self.objects())
        f = self.frame("t", "test", objects=[dict(bbox=[400, 10, 460, 50], label=-3)])
        feedback.transfer(self.p, "t")
        train, val, m = feedback.prepare(self.p, "b", self.root)
        self.assertEqual(len(train), 2)
        self.assertEqual(len(val), 0)
        self.assertEqual(m["original_images"], 2)
        self.assertEqual(sum(o["label"] >= 0 for r in train for o in r["objects"]), 3)
        self.assertEqual(db.snapshot(self.p, "test"), [])
        validate_job(
            dict(
                manifest=m,
                initialization="random",
                initial=None,
                historical_replay=False,
            )
        )
        f["capture_session"] = "different scene"
        with self.assertRaises(ValueError):
            feedback.prepare(self.p, "b", self.root)

    def test_new_pose_enters_cumulative_mapping(self):
        f = self.frame("a", objects=self.objects())
        a = feedback.add_pose(self.p, "new")
        new = self.frame(
            "t", "test", objects=[dict(bbox=[10, 10, 60, 40], label=a["index"])]
        )
        feedback.transfer(self.p, "t")
        train, _, m = feedback.prepare(self.p, "b", self.root)
        self.assertEqual(len(m["class_mapping"]["labels"]), 4)
        self.assertEqual(m["class_instances"][a["id"]], 1)
        self.assertEqual(len(train), 2)

    def test_draft_history_revision_and_idempotency(self):
        f = self.frame("a")
        obs = self.objects()
        db.annotate(self.p, "a", obs, False, 0)
        rev = f["annotation_revision"]
        db.annotate(self.p, "a", obs, False, 0)
        self.assertEqual(f["annotation_revision"], rev)
        with self.assertRaises(ValueError):
            db.annotate(self.p, "a", [], False, 0)
        self.assertEqual(len(f["objects"]), 3)
        self.assertEqual(len(f["annotation_history"]), 1)
        db.annotate(self.p, "a", obs[:2], False, rev)
        db.annotate(self.p, "a", obs, True, f["annotation_revision"])
        self.assertEqual(len(f["objects"]), 3)

    def test_unconfirmed_predictions_preserved_and_never_truth(self):
        f = self.frame("test", "test")
        f["reviewed"] = False
        run = "r"
        self.p["runs"] = [run]
        folder = self.root / "runs" / run
        db.write(folder / "config.json", self.p["schema"])
        db.write(
            folder / "tests" / "t" / "result.json",
            dict(
                selected_model=dict(run=run),
                rows=[
                    dict(
                        frame_id="test",
                        detections=[
                            dict(
                                bbox=[10, 10, 40, 40],
                                label=self.p["schema"]["labels"][0],
                            )
                        ],
                    )
                ],
            ),
        )
        feedback.import_prediction(self.p, run, "t", "test")
        self.assertTrue(f["objects"][0]["needs_review"])
        db.annotate(self.p, "test", [dict(bbox=[10, 10, 40, 40], label=2)], False)
        feedback.import_prediction(self.p, run, "t", "test")
        self.assertEqual(f["objects"][0]["label"], 2)
        self.assertEqual(len(f["feedback_source"]["raw_output"]["detections"]), 1)

    def test_loss_ignored_pixels_zero_gradient_negative_active(self):
        import torch
        from core.training import detection_loss

        obs = self.objects() + [
            dict(bbox=[400, 200, 512, 288], label=-1),
            dict(bbox=[400, 10, 460, 40], label=-3),
        ]
        z = torch.zeros(1, 8, 72, 128, requires_grad=True)
        loss, _ = detection_loss(z, [targets(obs)])
        loss.backward()
        self.assertEqual(float(z.grad[0, 0, 55, 110]), 0)
        self.assertGreater(float(z.grad[0, 0, 5, 105]), 0)

    def test_imports_require_explicit_same_scene_association(self):
        self.frame("a", objects=self.objects())
        f = self.frame("t", "test", objects=[dict(bbox=[400, 10, 450, 40], label=-3)])
        f["batch"] = "new_import"
        feedback.transfer(self.p, "t")
        with self.assertRaises(ValueError):
            feedback.prepare(self.p, "b", self.root)
        feedback.confirm_scene(self.p, "b")
        rows, _, _ = feedback.prepare(self.p, "b", self.root)
        self.assertEqual(len(rows), 2)

    def test_native_scene_confirmation_cannot_override_sensor_identity(self):
        a = self.frame("a", objects=self.objects())
        b = self.frame("t", "test", objects=self.objects())
        for f in (a, b):
            f.update(
                native_roi=True,
                origin=[10, 10],
                capture_session="s",
                imaging_identity="x",
            )
        b["imaging_identity"] = "different lighting"
        feedback.transfer(self.p, "t")
        feedback.confirm_scene(self.p, "b")
        with self.assertRaises(ValueError):
            feedback.prepare(self.p, "b", self.root)

    def test_lineage_alias_cannot_cross_test_split(self):
        a = self.frame("a", objects=self.objects())
        derived = dict(
            id="copy", role="test", sha256="new hash", parent_sha256=a["sha256"]
        )
        with self.assertRaises(ValueError):
            db.add_frames(self.p, [derived])

    def test_generic_augmentation_never_rotates_discrete_pose(self):
        from core.data import augment

        im = np.zeros((288, 512, 3), np.uint8)
        im[60:120, 10:70] = 120
        obs = self.objects()
        aug, t = augment(
            im, obs, np.random.default_rng(2), 1, self.p["schema"]["labels"]
        )
        self.assertTrue(np.all(aug[:30] == 0))
        self.assertEqual(t[1][:, 6].tolist(), [0, 1, 2])

    def test_training_failure_preserves_feedback_and_snapshot(self):
        from service import Service
        from unittest.mock import patch

        self.frame("a", objects=self.objects())
        f = self.frame("t", "test", objects=self.objects())
        feedback.transfer(self.p, "t")
        out = self.root / "runs" / "failed"
        db.write(out / "job.json", dict(frozen_annotations=copy.deepcopy(f["objects"])))
        s = Service()
        s.job = dict(project=self.p["id"])
        with patch.object(
            s, "subprocess", side_effect=RuntimeError("simulated worker failure")
        ):
            with self.assertRaises(RuntimeError):
                s.train_action(out)
        self.assertTrue(db.load(self.p["id"])["frames"][1]["feedback_pending"])
        self.assertEqual(db.read(out / "run_status.json")["state"], "error")
        self.assertEqual(db.read(out / "job.json")["frozen_annotations"], f["objects"])


if __name__ == "__main__":
    unittest.main()
