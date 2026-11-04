from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from console_board import Board


class FixedModelRoiTests(unittest.TestCase):
    def setUp(self):
        self.board = Board({})
        self.board.fresh = MagicMock(
            return_value={
                "inference": {"async_inference": {"model_input_size": [512, 288]}}
            }
        )
        self.board.transaction = MagicMock(return_value=({"ok": True}, "/unused"))
        self.job = SimpleNamespace()

    def test_center_position_confidence_and_confirmation_remain_controllable(self):
        for x, y, confidence, hits in [
            (704, 396, 85, 3),
            (600, 300, 60, 1),
            (800, 500, 90, 5),
        ]:
            with self.subTest(position=(x, y), confidence=confidence, hits=hits):
                values = dict(
                    rectangle=[x, y, 512, 288],
                    confidence_percent=confidence,
                    track_hits=hits,
                )
                self.board.configure(values, self.job)
                body = self.board.transaction.call_args.args[1]
                self.assertEqual(body, dict(operation="configure", values=values))

    def test_changed_dimensions_are_rejected_before_any_transaction(self):
        for size in ([793, 317], [600, 300], [511, 288], [512, 289]):
            with self.subTest(size=size):
                with self.assertRaisesRegex(ValueError, "ROI"):
                    self.board.configure(
                        dict(
                            rectangle=[600, 300, *size],
                            confidence_percent=85,
                            track_hits=3,
                        ),
                        self.job,
                    )
        self.board.transaction.assert_not_called()

    def test_missing_actual_model_dimensions_cannot_apply_guessed_settings(self):
        self.board.fresh.return_value = {"inference": {}}
        with self.assertRaisesRegex(ValueError, "模型输入尺寸"):
            self.board.configure(
                dict(
                    rectangle=[704, 396, 512, 288], confidence_percent=85, track_hits=3
                ),
                self.job,
            )
        self.board.transaction.assert_not_called()


if __name__ == "__main__":
    unittest.main()
