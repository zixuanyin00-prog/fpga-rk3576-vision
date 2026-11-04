from pathlib import Path
import copy
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.environment15 import approved_profile
from core.pc_testing import same_parameters


class CapturePreservation(unittest.TestCase):
    def state(self, automatic):
        identity = dict(origin=[100, 200])
        board = dict(
            recorded_monotonic=100,
            identity=identity,
            busy=False,
            sensor=dict(
                sensor_transition=False,
                sensor_register_readback=True,
                shr0=2146,
                gain_code=60,
                vmax=2250,
                hmax=550,
            ),
            isp=dict(
                config=dict(
                    rg=370,
                    bg=289,
                    contrast=264,
                    brightness=-14,
                    gaussian=0,
                    saturation=256,
                    he=0,
                ),
                wb_auto=automatic,
            ),
            automatic=dict(
                config=dict(frozen=True),
                enhancement=dict(
                    revision=7,
                    effective_revision=7,
                    gain_q8=270,
                    strength_q8=64,
                    knots=[0, 32, 64, 96, 128, 160, 192, 224, 256],
                ),
            ),
        )
        return board, dict(recorded_monotonic=100, environment_identity=identity)

    def test_sampling_preserves_both_wb_modes_without_mutation(self):
        for automatic in (False, True):
            with self.subTest(automatic=automatic):
                board, runtime = self.state(automatic)
                before = copy.deepcopy(board)
                profile = copy.deepcopy(
                    approved_profile(board, runtime, 100, [100, 200])
                )
                self.assertEqual(board, before)
                self.assertEqual(profile["isp"]["wb_auto"], automatic)
                self.assertEqual(
                    profile["white_balance_policy"],
                    "automatic_preserved" if automatic else "manual_fixed",
                )
                self.assertEqual(same_parameters(profile, dict(board=board)), [])
                board["isp"] = copy.deepcopy(board["isp"])
                board["isp"]["wb_auto"] = not automatic
                self.assertIn(
                    "white balance mode", same_parameters(profile, dict(board=board))
                )

    def test_unpaused_host_auto_and_numeric_drift_still_rejected(self):
        board, runtime = self.state(True)
        profile = copy.deepcopy(approved_profile(board, runtime, 100, [100, 200]))
        board["isp"]["config"] = dict(board["isp"]["config"], brightness=-13)
        self.assertIn("ISP.brightness", same_parameters(profile, dict(board=board)))
        board["automatic"]["config"] = dict(frozen=False, brightness=True)
        with self.assertRaises(ValueError):
            approved_profile(board, runtime, 100, [100, 200])


if __name__ == "__main__":
    unittest.main()
