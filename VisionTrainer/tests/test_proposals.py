from pathlib import Path
import sys
import unittest
import copy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.proposals import annotation_predictions, supplemental


class ProposalTests(unittest.TestCase):
    def test_supplement_preserves_human_geometry_labels_and_pending_state(self):
        original = [
            dict(bbox=[100, 100, 170, 150], label=1, needs_review=False),
            dict(bbox=[190, 100, 260, 150], label=2, needs_review=True),
        ]
        frozen = copy.deepcopy(original)
        proposed = [
            dict(bbox=[110, 102, 160, 149], label=0),
            dict(bbox=[194, 100, 258, 150], label=0),
            dict(bbox=[300, 100, 365, 150], label=0),
        ]
        result, count = supplemental(original, proposed)
        self.assertEqual(original, frozen)
        self.assertEqual(result[:2], frozen)
        self.assertEqual(count, 1)
        self.assertEqual(result[2]["label"], -2)
        self.assertTrue(result[2]["needs_review"])

    def test_pc_tiles_keep_original_untouched_and_map_back_to_native_coordinates(self):
        image = np.zeros((288, 512, 3), np.uint8)
        original = image.copy()
        calls = []

        def predict(tile):
            calls.append(tile.shape)
            return (
                [dict(bbox=[100, 60, 200, 120], objectness=0.8)]
                if len(calls) == 2
                else []
            )

        rows = annotation_predictions(predict, image)
        self.assertEqual(len(calls), 10)
        self.assertTrue(all(s == (288, 512, 3) for s in calls))
        self.assertTrue(np.array_equal(image, original))
        self.assertEqual(len(rows), 1)
        np.testing.assert_allclose(
            rows[0]["bbox"], [100 * 341 / 512, 40, 200 * 341 / 512, 80]
        )

    def test_tile_edge_fragments_are_not_invented_as_full_bodies(self):
        calls = []

        def predict(tile):
            calls.append(1)
            return (
                [] if len(calls) == 1 else [dict(bbox=[-2, 30, 80, 90], objectness=0.9)]
            )

        self.assertEqual(
            annotation_predictions(predict, np.zeros((288, 512, 3), np.uint8)), []
        )


if __name__ == "__main__":
    unittest.main()
