from pathlib import Path
import tempfile
import unittest
import sys
import hashlib
from unittest.mock import patch
import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import store as db
from core import box_assistant as boxes
from core.box_model import ScratchBoxNet, targets
from service import Service
from core import box_v2


class BoxAssistantTests(unittest.TestCase):
    def test_regressed_candidate_does_not_replace_active_model(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            boxes, "HOME", Path(directory)
        ):
            active = dict(run="old", ready=True)
            db.write(boxes.HOME / "active.json", active)
            service = Service()
            service.job = {}

            def invoke(mode, path, script):
                db.write(
                    Path(path).parent / "result.json", {"promotion_allowed": False}
                )

            with patch.object(
                boxes, "training_frames", return_value=[{}] * 5
            ), patch.object(boxes, "status", return_value=active), patch.object(
                service, "subprocess", side_effect=invoke
            ):
                with self.assertRaisesRegex(ValueError, "不训练或应用历史画框助手"):
                    service.train_box_action()
            self.assertEqual(db.read(boxes.HOME / "active.json"), active)

    def test_refresh_preserves_confirmed_and_draft_boxes(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            db, "DATA", Path(directory)
        ):
            p = db.create("test")
            p["frames"] = [
                dict(id="draft", annotated_at="saved", objects=[{"label": 2}]),
                dict(id="confirmed", reviewed=True, objects=[{"label": 0}]),
                dict(id="untouched", objects=[]),
            ]
            db.save(p)
            service = Service()
            service.box_selection = dict(project=p["id"], architecture="synthetic_test")
            with patch.object(
                service,
                "run_suggestions",
                return_value={
                    k: [{"bbox": [10, 10, 30, 30]}]
                    for k in ["draft", "confirmed", "untouched"]
                },
            ), patch.object(boxes, "status", return_value={"architecture": "test"}):
                service.suggest_action(p["id"], [], Path(directory))
            fresh = db.load(p["id"])
            self.assertEqual(fresh["frames"][:2], p["frames"][:2])
            self.assertEqual(len(fresh["frames"][2]["proposals"]), 1)

    def test_each_scene_has_held_development_frames(self):
        rows = [dict(source="historical_reviewed_train") for _ in range(36)] + [
            dict(source="confirmed_project_train", project="scene") for _ in range(40)
        ]
        held = boxes.development_indices(rows)
        self.assertEqual(len([i for i in held if i < 36]), 6)
        self.assertEqual(len([i for i in held if i >= 36]), 8)
        self.assertEqual(len(set(held)), 14)

    def test_partial_scene_does_not_create_negative_training_examples(self):
        im = np.zeros((288, 512, 3), np.uint8)
        examples = box_v2.verifier_examples(
            [(im, [dict(bbox=[50, 100, 150, 160])])],
            np.random.default_rng(12),
            count=12,
            positive_only=True,
        )
        self.assertTrue(examples)
        self.assertTrue(all(y == 1 for _, y in examples))

    def test_checkpoint_specific_threshold_is_used(self):
        with patch.object(box_v2, "locate", return_value=[]) as locate:
            box_v2.locate_configured(
                None, None, None, {"inference": {"joint_threshold": 0.65}}
            )
        self.assertEqual(locate.call_args.kwargs["joint_threshold"], 0.65)

    def test_v2_uses_finer_grid_and_no_pretrained_checkpoint(self):
        with patch(
            "torch.load", side_effect=AssertionError("checkpoint load forbidden")
        ):
            net = box_v2.BoxNetV2()
            self.assertEqual(
                tuple(net(torch.zeros(1, 3, 288, 512)).shape), (1, 5, 72, 128)
            )
            verifier = box_v2.PatchVerifier()
            self.assertEqual(tuple(verifier(torch.zeros(2, 3, 64, 96)).shape), (2,))
        heat, truth = targets([dict(bbox=[80, 80, 160, 120], ignore=False)], stride=4)
        self.assertEqual(heat.shape, (72, 128))
        self.assertEqual(int((heat == 1).sum()), 1)

    def test_background_examples_do_not_contain_reviewed_objects(self):
        objects = [
            dict(bbox=[30, 90, 135, 160]),
            dict(bbox=[180, 85, 290, 160]),
            dict(bbox=[365, 90, 470, 155], ignore=True),
        ]
        rng = np.random.default_rng(123)
        for _ in range(100):
            box = box_v2.negative_box(objects, rng)
            self.assertIsNotNone(box)
            self.assertTrue(
                all(box_v2.intersection(box, o["bbox"]) == 0 for o in objects)
            )

    def test_strong_detector_can_retain_pale_body_without_accepting_weak_background(
        self,
    ):
        image = np.zeros((288, 512, 3), np.uint8)
        candidates = [
            dict(bbox=[100, 100, 160, 145], objectness=0.9),
            dict(bbox=[200, 50, 260, 95], objectness=0.4),
        ]

        class Verify:
            def parameters(self):
                return iter([torch.zeros(1)])

            def __call__(self, x):
                return torch.logit(torch.tensor([0.45, 0.52]))

        with patch.object(box_v2, "annotation_predictions", return_value=candidates):
            found = box_v2.locate(None, Verify(), image)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["bbox"], candidates[0]["bbox"])

    def test_scratch_network_does_not_load_any_checkpoint(self):
        with patch(
            "torch.load", side_effect=AssertionError("checkpoint load forbidden")
        ):
            net = ScratchBoxNet()
            self.assertEqual(
                tuple(net(torch.zeros(1, 3, 288, 512)).shape), (1, 5, 36, 64)
            )
        heat, truth = targets([dict(bbox=[80, 80, 160, 120], ignore=False)])
        self.assertEqual(truth.shape, (1, 6))
        self.assertEqual(int((heat == 1).sum()), 1)

    def test_only_reviewed_train_boxes_enter_helper(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            db, "DATA", Path(directory) / "data"
        ), patch.object(boxes, "HOME", Path(directory) / "helper"):
            db.DATA.mkdir()
            db.write(
                boxes.HOME / "seed/annotations.json",
                dict(frames=[], annotation_source="fixture"),
            )
            p = db.create("test")
            rows = []
            for i, (role, reviewed, pending) in enumerate(
                [
                    ("train", True, False),
                    ("validation", True, False),
                    ("test", True, False),
                    ("train", False, False),
                    ("train", True, True),
                ]
            ):
                raw = bytes([i])
                filename = f"{i}.rgb"
                (db.project_path(p["id"]) / filename).write_bytes(raw)
                rows.append(
                    dict(
                        id=str(i),
                        role=role,
                        reviewed=reviewed,
                        file=filename,
                        sha256=hashlib.sha256(raw).hexdigest(),
                        objects=[
                            dict(bbox=[10, 10, 30, 30], label=2, needs_review=pending)
                        ],
                    )
                )
            p["frames"] = rows
            db.save(p)
            found = boxes.training_frames()
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["frame"], "0")
            self.assertNotIn("label", found[0]["objects"][0])

    def test_suggestions_use_only_dedicated_worker_and_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            db, "DATA", Path(directory) / "data"
        ), patch.object(boxes, "HOME", Path(directory) / "helper"):
            db.DATA.mkdir()
            p = db.create("test")
            out = Path(directory) / "suggest"
            service = Service()
            service.job = {}
            model = boxes.HOME / "runs/standalone/boxes.pt"
            model.parent.mkdir(parents=True)
            model.write_bytes(b"synthetic worker routing fixture; never loaded")
            service.box_selection = dict(
                project=p["id"],
                run="standalone",
                trained_at="synthetic fixture",
                sha256=hashlib.sha256(model.read_bytes()).hexdigest(),
            )

            def invoke(mode, path, script, **kwargs):
                job = db.read(path)
                self.assertEqual(script, "box_worker.py")
                self.assertEqual(mode, "suggest")
                self.assertEqual(Path(job["model"]).name, "boxes.pt")
                self.assertFalse(job["recognition_model_used"])
                self.assertNotIn("config", job)
                db.write(out / "proposals.json", {"f": []})

            with patch.object(
                boxes, "status", return_value={"ready": True, "run": "standalone"}
            ), patch.object(service, "subprocess", side_effect=invoke):
                self.assertEqual(service.run_suggestions(p["id"], [], out), {"f": []})


if __name__ == "__main__":
    unittest.main()
