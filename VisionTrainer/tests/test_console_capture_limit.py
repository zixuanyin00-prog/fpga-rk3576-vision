from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import console_store as db
from console_board import Board
from console_workflow import Workflow


class CaptureLimitTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        data = patch.object(db, "DATA", Path(temp.name))
        data.start()
        self.addCleanup(data.stop)
        self.flow = db.create("capture limit")
        self.workflow = Workflow.__new__(Workflow)
        self.workflow.settings = {}
        self.workflow.jobs = MagicMock()
        network = patch(
            "console_workflow.Board", side_effect=AssertionError("real board forbidden")
        )
        self.board = network.start()
        self.addCleanup(network.stop)

    def test_area_limit_accepts_native_rectangles_and_exact_boundary(self):
        for rect in (
            [704, 396, 512, 288],
            [0, 0, 960, 540],
            [960, 540, 960, 540],
            [0, 0, 1800, 288],
        ):
            with self.subTest(rect=rect):
                self.assertEqual(db.rectangle(rect, training=True), rect)
        for rect in ([0, 0, 960, 541], [0, 0, 1800, 289], [0, 0, 1920, 1080]):
            with self.subTest(rect=rect), self.assertRaisesRegex(ValueError, "518,400"):
                db.rectangle(rect, training=True)
        with self.assertRaisesRegex(ValueError, "至少512×288"):
            db.rectangle([0, 0, 511, 288], training=True)
        with self.assertRaisesRegex(ValueError, "1920×1080"):
            db.rectangle([961, 0, 960, 540], training=True)

    def test_request_rejects_oversize_freeze_before_queueing(self):
        with self.assertRaisesRegex(ValueError, "518,400"):
            self.workflow.dispatch(
                "freeze", dict(flow=self.flow["id"], rectangle=[0, 0, 960, 541]), "page"
            )
        self.workflow.jobs.submit.assert_not_called()
        self.board.assert_not_called()
        self.assertEqual(db.load(self.flow["id"])["rectangle"], self.flow["rectangle"])

    def test_old_flow_cannot_queue_capture_append_or_new_image_test(self):
        self.flow["rectangle"] = [0, 0, 1920, 1080]
        db.save(self.flow)
        for action, args in [
            ("capture", {}),
            ("capture", {"append": True}),
            ("test", {}),
        ]:
            with self.subTest(action=action, args=args), self.assertRaisesRegex(
                ValueError, "518,400"
            ):
                self.workflow.dispatch(
                    action, dict(flow=self.flow["id"], **args), "page"
                )
        self.workflow.jobs.submit.assert_not_called()
        self.board.assert_not_called()
        self.assertEqual(db.load(self.flow["id"])["rectangle"], [0, 0, 1920, 1080])

    def test_exact_boundary_can_queue_freeze_capture_and_test(self):
        self.flow["rectangle"] = [960, 540, 960, 540]
        db.save(self.flow)
        for action in ("freeze", "capture", "test"):
            args = dict(flow=self.flow["id"])
            if action == "freeze":
                args["rectangle"] = self.flow["rectangle"]
            self.workflow.dispatch(action, args, "page")
        self.assertEqual(self.workflow.jobs.submit.call_count, 3)
        self.board.assert_not_called()

    def test_reloaded_capture_is_checked_before_board_resource_or_connection(self):
        self.flow.update(rectangle=[0, 0, 1920, 1080], profile={"digest": "unchanged"})
        db.save(self.flow)
        for role in ("train", "test"):
            with self.subTest(role=role), self.assertRaisesRegex(ValueError, "518,400"):
                self.workflow.capture(self.flow["id"], role, SimpleNamespace())
        self.workflow.jobs.resource.assert_not_called()
        self.board.assert_not_called()

    def test_board_adapter_refuses_oversize_before_sending_transaction(self):
        board = Board({})
        board.transaction = MagicMock()
        with self.assertRaisesRegex(ValueError, "518,400"):
            board.capture([0, 0, 960, 541], 1, db.DATA / "unused", SimpleNamespace())
        board.transaction.assert_not_called()
        self.assertFalse((db.DATA / "unused").exists())


if __name__ == "__main__":
    unittest.main()
