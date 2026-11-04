from pathlib import Path
import tempfile
import sys
import unittest
import json
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "bowl_training_20261023/runtime")
)
from training_capture_bridge import TrainingCaptureBridge
from core import store as db
from core.capture import origin, fps_summary
from worker import predictions


class RegionTests(unittest.TestCase):
    def test_display_requires_distinct_sources_not_redraws(self):
        import copy

        point = dict(
            fresh=True,
            boot_id="same",
            runtime=dict(
                pid=7,
                recorded_monotonic=1,
                preview=dict(preview_frames=100, source_gaps=0),
                inference=dict(roi_calls_per_second=30),
            ),
            displays={
                "HDMI-1": dict(
                    recorded_monotonic=1,
                    unique_frames=100,
                    skipped_source_frames=0,
                    presentation=dict(submitted=100, completed=100),
                )
            },
        )
        last = copy.deepcopy(point)
        last["runtime"]["recorded_monotonic"] = 61
        last["runtime"]["preview"]["preview_frames"] = 3700
        last["displays"]["HDMI-1"].update(
            recorded_monotonic=61,
            unique_frames=3700,
            presentation=dict(submitted=3700, completed=3700),
        )
        self.assertTrue(fps_summary(point, last)["verified"])
        last["displays"]["HDMI-1"]["unique_frames"] = 100
        self.assertFalse(fps_summary(point, last)["verified"])
        last["displays"]["HDMI-1"]["unique_frames"] = 3700
        last["displays"]["HDMI-1"]["skipped_source_frames"] = 1
        self.assertFalse(fps_summary(point, last)["verified"])
        last["displays"]["HDMI-1"]["skipped_source_frames"] = 0
        last["displays"]["HDMI-1"]["presentation"]["completed"] = 100
        self.assertFalse(fps_summary(point, last)["verified"])

    def test_bounds(self):
        self.assertEqual(origin([1408, 792]), [1408, 792])
        for value in [[-1, 1], [1409, 1], [1, 793], [1.2, 3], [True, 2], None]:
            with self.assertRaises(ValueError):
                origin(value)

    def test_bridge_native_bytes_and_malformed_request_isolation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bridge = TrainingCaptureBridge(root)
            raw = np.arange(512 * 288 * 3, dtype=np.uint8).reshape(288, 512, 3)
            cam = SimpleNamespace(latest=SimpleNamespace(rgb=raw, source_frame_id=1))
            next_id = iter([2, 3, 4])
            cam.acquire = lambda roi, x, y: (
                0,
                SimpleNamespace(
                    rgb=raw, source_frame_id=next(next_id), roi=roi, x=x, y=y
                ),
            )
            applied = []
            (root / "training_control/request.json").write_text("{bad")
            self.assertFalse(bridge.poll(cam, applied.append))
            bridge.next_poll = 0
            (root / "training_control/request.json").write_text(
                json.dumps(dict(id="valid", kind="region", origin=[800, 470]))
            )
            self.assertTrue(bridge.poll(cam, applied.append))
            self.assertEqual(applied[0]["origin"], [800, 470])
            self.assertEqual(
                (root / "training_control/valid/native_0.rgb").read_bytes(),
                raw.tobytes(),
            )
            self.assertEqual(
                json.loads((root / "training_control/valid/result.json").read_text())[
                    "frames"
                ][2]["source_frame_id"],
                4,
            )

    def test_bridge_rejects_changes_during_batch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bridge = TrainingCaptureBridge(root)
            applied = []
            (root / "training_control/request.json").write_text(
                json.dumps(dict(id="busy", kind="region", origin=[800, 470]))
            )
            self.assertFalse(bridge.poll(None, applied.append, busy=True))
            self.assertEqual(applied, [])
            self.assertFalse(
                json.loads((root / "training_control/busy/result.json").read_text())[
                    "ok"
                ]
            )

    def test_complete_objects_outside_old_vertical_band_not_dropped(self):
        import torch

        candidates = [
            dict(bbox=[30, 220, 80, 270]),
            dict(bbox=[100, 10, 160, 50]),
            dict(bbox=[20, 260, 80, 300]),
        ]
        net = lambda x: [torch.zeros(1)]
        net.parameters = lambda: iter([torch.zeros(1)])
        with patch("worker.decode", return_value=candidates):
            result = predictions(
                net,
                np.zeros((288, 512, 3), np.uint8),
                dict(object_threshold=0.15, state_threshold=0.85, labels=[]),
            )
        self.assertEqual(result, candidates[:2])

    def test_quarantined_bad_batch_never_blocks_or_enters_training(self):
        p = dict(
            schema=db.schema([]),
            frames=[dict(role="train", excluded=True, reviewed=False, objects=[])],
        )
        self.assertFalse(db.training_ready(p)[0])
        p["frames"].append(
            dict(
                role="train",
                reviewed=True,
                objects=[dict(label=i) for i in range(3) for _ in range(10)],
            )
        )

        self.assertFalse(db.training_ready(p)[0])
        self.assertIn("15张", db.training_ready(p)[1])


if __name__ == "__main__":
    unittest.main()
