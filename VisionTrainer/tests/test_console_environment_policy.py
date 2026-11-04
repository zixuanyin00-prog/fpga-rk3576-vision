from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import console_store as db
from console_workflow import Workflow


class EnvironmentPolicyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        data = patch.object(db, "DATA", Path(temp.name))
        data.start()
        self.addCleanup(data.stop)
        self.flow = db.create("environment policy")
        self.flow.update(
            capture_batch="new-batch",
            profile=dict(
                digest="training-profile", processing_identity=dict(fpga_epoch=1)
            ),
        )
        db.save(self.flow)
        self.workflow = Workflow.__new__(Workflow)
        self.workflow.settings = {}
        self.workflow.jobs = SimpleNamespace(resource=lambda *args: nullcontext())
        self.job = SimpleNamespace(
            update=MagicMock(),
            check=MagicMock(),
            cancel=SimpleNamespace(is_set=lambda: False),
        )
        self.board = MagicMock()
        self.board.__enter__.return_value = self.board
        self.board.__exit__.return_value = False
        self.board.boot = "boot"
        self.board.profile.return_value = dict(
            digest="current-test-profile", sensor={"shr0": 42}
        )
        self.board.capture.return_value = (
            [
                dict(
                    file="native_0.rgb",
                    source_frame_id=1,
                    sha256="fresh",
                    channel=2,
                    request_id=1,
                    fpga_epoch=99,
                    processing_revision=7,
                    enhance_revision=8,
                )
            ],
            dict(protocol="native-rectangle-v2"),
        )
        self.board.ensure_profile.side_effect = ValueError("actual environment changed")
        board = patch("console_workflow.Board", return_value=self.board)
        board.start()
        self.addCleanup(board.stop)

    def test_test_capture_uses_current_environment_and_preserves_training_profile(self):
        rows = self.workflow.capture(self.flow["id"], "test", self.job)
        self.assertEqual(rows[0]["profile_digest"], "current-test-profile")
        after = db.load(self.flow["id"])
        self.assertEqual(after["profile"], self.flow["profile"])
        self.assertEqual(after["capture_batch"], "new-batch")
        self.assertEqual(
            after["capture_profiles"][rows[0]["batch"]]["sensor"], {"shr0": 42}
        )
        self.board.ensure_profile.assert_not_called()

    def test_testing_does_not_require_freezing_new_environment_first(self):
        self.flow["profile"] = None
        db.save(self.flow)
        self.assertEqual(
            len(self.workflow.capture(self.flow["id"], "test", self.job)), 1
        )
        self.board.ensure_profile.assert_not_called()

    def test_test_record_preserves_training_and_actual_capture_environments(self):
        out = db.folder(self.flow["id"]) / "runs" / "old-run"
        manifest = dict(scene=dict(profile={"digest": "old-model-profile"}))
        selected = dict(
            run="old-run", trained_at="2026-10-28T18:00:00+08:00", sha256="selected"
        )
        self.workflow.selected_run = MagicMock(
            return_value=(self.flow, out, manifest, selected)
        )

        def worker(mode, spec, dest, job):
            db.write(
                dest / "predictions.json",
                dict(rows=[dict(frame=f["id"], detections=[]) for f in spec["frames"]]),
            )

        self.workflow.run_worker = worker
        result = self.workflow.test(self.flow["id"], {}, self.job)
        test = db.load(self.flow["id"])["tests"][0]
        self.assertEqual(test["training_profile"]["digest"], "old-model-profile")
        self.assertEqual(test["captured_profile"]["digest"], "current-test-profile")
        self.assertFalse(result["complete"])
        self.board.ensure_profile.assert_not_called()

    def test_training_capture_still_rejects_changed_environment_before_capture(self):
        with self.assertRaisesRegex(ValueError, "actual environment changed"):
            self.workflow.capture(self.flow["id"], "train", self.job)
        self.board.capture.assert_not_called()

    def test_training_capture_still_requires_first_step_profile(self):
        self.flow["profile"] = None
        db.save(self.flow)
        with self.assertRaisesRegex(ValueError, "第一步"):
            self.workflow.capture(self.flow["id"], "train", self.job)
        self.board.capture.assert_not_called()

    def test_training_capture_still_checks_actual_processing_identity(self):
        self.board.ensure_profile.side_effect = None
        with self.assertRaisesRegex(ValueError, "公共图像处理版本已改变"):
            self.workflow.capture(self.flow["id"], "train", self.job)
        self.assertEqual(self.board.ensure_profile.call_count, 2)
        self.assertEqual(db.load(self.flow["id"])["frames"], [])

    def test_different_batch_model_still_requires_explicit_time_and_intact_hash(self):
        run = "selected-run"
        out = db.folder(self.flow["id"]) / "runs" / run
        out.mkdir(parents=True)
        (out / "detector.pt").write_bytes(b"local fixture only")
        manifest = dict(
            batch="old-batch",
            training_completed_beijing="2026-10-28T18:00:00+08:00",
            output_hashes={"detector.pt": db.sha((out / "detector.pt").read_bytes())},
        )
        db.write(out / "training_manifest.json", manifest)
        self.flow["runs"] = [dict(id=run)]
        db.save(self.flow)
        args = dict(
            run=run, trained_at=manifest["training_completed_beijing"], confirmed=True
        )
        self.assertEqual(
            self.workflow.selected_run(self.flow["id"], args)[2]["batch"], "old-batch"
        )
        with self.assertRaisesRegex(ValueError, "明确选择"):
            self.workflow.selected_run(self.flow["id"], dict(args, confirmed=False))
        (out / "detector.pt").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "模型与训练记录不一致"):
            self.workflow.selected_run(self.flow["id"], args)

    def test_cross_environment_test_feedback_cannot_enter_frozen_training_batch(self):
        run = "selected-run"
        out = db.folder(self.flow["id"]) / "runs" / run
        db.write(out / "training_manifest.json", dict(batch="new-batch"))
        self.flow["frames"] = [
            dict(id="test-frame", profile_digest="other-environment", role="test")
        ]
        self.flow["tests"] = [dict(id="test", run=run, frames=["test-frame"])]
        db.save(self.flow)
        with self.assertRaisesRegex(ValueError, "不能直接混入"):
            self.workflow.feedback(
                self.flow["id"], dict(test="test", frames=["test-frame"])
            )
        self.assertEqual(db.load(self.flow["id"])["frames"][0]["role"], "test")


class RuntimeChoicePolicyTests(unittest.TestCase):
    def test_old_imaging_model_choice_keeps_hash_and_single_use_protection(self):
        source = (
            Path(__file__).resolve().parents[2]
            / "three_stream_20261028/runtime/selected_model_gate.py"
        )
        spec = importlib.util.spec_from_file_location(
            "environment_policy_model_gate", source
        )
        gate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gate)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            model = root / "model.rknn"
            config = root / "config.json"
            ticket = root / "selection.json"
            model.write_bytes(b"local hash fixture, never loaded")
            config.write_text(
                json.dumps(
                    dict(
                        image_processing="legacy_rgb", input_transform="native_tiles_v1"
                    )
                )
            )
            choice = dict(
                boot_id="local-boot",
                nonce="explicit-choice",
                expires_monotonic=time.monotonic() + 60,
                confirmed=True,
                training_completed_beijing="2026-10-28T18:00:00+08:00",
                fine_model_path=str(model),
                fine_config_path=str(config),
                fine_sha256=hashlib.sha256(model.read_bytes()).hexdigest(),
                config_sha256=hashlib.sha256(config.read_bytes()).hexdigest(),
            )
            ticket.write_text(json.dumps(choice))
            read_text = Path.read_text

            def read(path, *args, **kwargs):
                return (
                    "local-boot"
                    if path == Path("/proc/sys/kernel/random/boot_id")
                    else read_text(path, *args, **kwargs)
                )

            with patch.dict(
                os.environ,
                dict(
                    VISION_MODEL_SELECTION_TICKET=str(ticket),
                    VISION_MODEL_SELECTION_NONCE="explicit-choice",
                ),
            ), patch.object(Path, "read_text", read):
                model.write_bytes(b"tampered")
                with self.assertRaisesRegex(
                    gate.ModelSelectionRequired, "artifact hash mismatch"
                ):
                    gate.require_model_selection()
                model.write_bytes(b"local hash fixture, never loaded")
                self.assertEqual(
                    gate.require_model_selection()["fine_sha256"], choice["fine_sha256"]
                )
                with self.assertRaises(gate.ModelSelectionRequired):
                    gate.require_model_selection()


if __name__ == "__main__":
    unittest.main()
