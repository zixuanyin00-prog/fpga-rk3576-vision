from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import console_store as db
from console_workflow import Workflow


class ConsoleModelSwitchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        data = patch.object(db, "DATA", Path(temp.name))
        data.start()
        self.addCleanup(data.stop)
        p = db.create("1027")
        p.update(capture_batch="batch", profile={"digest": "profile"})
        p["operator_note"] = "must remain"
        db.save(p)
        self.identity = p["id"]
        self.selected = dict(
            run="selected-run",
            batch="batch",
            fine_sha256="selected-hash",
            scene={"profile": {"digest": "profile"}, "rectangle": [704, 396, 512, 288]},
        )
        self.workflow = Workflow.__new__(Workflow)
        self.workflow.settings = {}
        self.workflow.jobs = SimpleNamespace(resource=lambda *args: nullcontext())
        self.workflow.selections = {
            "choice": dict(
                flow=p["id"], selection=self.selected, expires=time.monotonic() + 60
            )
        }
        self.job = SimpleNamespace(
            update=MagicMock(), check=MagicMock(), protected=False
        )
        self.board = MagicMock()
        self.board.__enter__.return_value = self.board
        self.board.__exit__.return_value = False
        guard = patch("console_workflow.Board", return_value=self.board)
        self.factory = guard.start()
        self.addCleanup(guard.stop)
        deploy = patch(
            "console_model_deploy.deploy_model",
            return_value=dict(
                state="loaded",
                model_only=True,
                runtime_preserved=True,
                site_config_preserved=True,
            ),
        )
        self.deploy = deploy.start()
        self.addCleanup(deploy.stop)
        self.args = dict(flow=p["id"], nonce="choice", confirmed=True)

    def test_model_switch_passes_no_runtime_or_site_settings_and_keeps_flow(self):
        result = self.workflow.deploy(self.identity, self.args, self.job)
        self.assertTrue(result["model_only"])
        self.deploy.assert_called_once()
        board, out, selected, bundle, job = self.deploy.call_args.args
        self.assertIs(board, self.board)
        self.assertEqual(selected, self.selected)
        self.assertEqual(out, db.folder(self.identity) / "runs" / "selected-run")
        self.assertEqual({p.name for p in bundle.iterdir()}, {"result.json"})
        after = db.load(self.identity)
        self.assertEqual(after["operator_note"], "must remain")
        self.assertEqual(after["profile"], {"digest": "profile"})
        self.assertEqual(after["rectangle"], [704, 396, 512, 288])
        self.assertEqual(len(after["deployments"]), 1)
        self.board.command.assert_not_called()
        self.board.s.put.assert_not_called()
        self.board.ensure_profile.assert_not_called()

    def test_explicit_model_switch_allows_different_current_environment_and_batch(self):
        p = db.load(self.identity)
        p.update(capture_batch="new-batch", profile={"digest": "new-environment"})
        db.save(p)
        self.board.ensure_profile.side_effect = AssertionError(
            "training environment must not gate deployment"
        )
        self.workflow.deploy(self.identity, self.args, self.job)
        self.deploy.assert_called_once()
        self.board.ensure_profile.assert_not_called()
        after = db.load(self.identity)
        self.assertEqual(after["profile"], {"digest": "new-environment"})
        self.assertEqual(after["capture_batch"], "new-batch")

    def test_old_page_cannot_smuggle_roi_confidence_hits_into_model_switch(self):
        self.args["values"] = dict(
            rectangle=[0, 0, 793, 317], confidence_percent=85, track_hits=3
        )
        with self.assertRaisesRegex(ValueError, "values"):
            self.workflow.deploy(self.identity, self.args, self.job)
        self.factory.assert_not_called()
        self.deploy.assert_not_called()
        self.assertIn("choice", self.workflow.selections)

    def test_runtime_payload_argument_is_rejected_before_connecting(self):
        self.args["files"] = {"native_view.py": "different UI"}
        with self.assertRaisesRegex(ValueError, "files"):
            self.workflow.deploy(self.identity, self.args, self.job)
        self.factory.assert_not_called()
        self.deploy.assert_not_called()

    def test_choice_or_scene_mismatch_never_reaches_model_switch(self):
        self.args["confirmed"] = False
        with self.assertRaisesRegex(ValueError, "选择"):
            self.workflow.deploy(self.identity, self.args, self.job)
        self.factory.assert_not_called()
        self.deploy.assert_not_called()

    def test_plan_rejection_keeps_saved_flow_without_deployment_claim(self):
        self.deploy.side_effect = ValueError("forbidden module: shared_preview.py")
        with self.assertRaisesRegex(ValueError, "shared_preview"):
            self.workflow.deploy(self.identity, self.args, self.job)
        self.assertEqual(db.load(self.identity)["deployments"], [])


if __name__ == "__main__":
    unittest.main()
