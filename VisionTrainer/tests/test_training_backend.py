import unittest
import numpy as np
import torch
from core.training import scene_augment, detection_loss, score_frames, infer_all
from core.data import targets
from core.model import ResistorNet


class TrainingBackendTests(unittest.TestCase):
    def test_translation_keeps_pose_and_center(self):
        im = np.zeros((288, 512, 3), np.uint8)
        im[100:180, 100:220] = 200
        obj = [dict(bbox=[100, 100, 220, 180], label=1)]
        aug, (_, truth) = scene_augment(im, obj, np.random.default_rng(8))
        ys, xs = np.where(aug[:, :, 0] > 100)
        self.assertEqual(int(truth[0, 6]), 1)
        np.testing.assert_allclose(
            [(truth[0, 1] + truth[0, 4]) * 4, (truth[0, 0] + truth[0, 5]) * 4],
            [(xs.min() + xs.max() + 1) / 2, (ys.min() + ys.max() + 1) / 2],
        )

    def test_loss_gradient_and_bn_update(self):
        torch.set_num_threads(2)
        net = ResistorNet(states=4).train()
        x = torch.randn(2, 3, 288, 512)
        t = targets([dict(bbox=[100, 100, 220, 180], label=1)])
        loss, _ = detection_loss(net.head(net.encode(x)), [t, t])
        loss.backward()
        self.assertTrue(torch.isfinite(loss))
        self.assertGreater(float(net.backbone[0][0].weight.grad.norm()), 0)
        self.assertTrue(
            all(
                int(v) == 1
                for k, v in net.state_dict().items()
                if k.endswith("num_batches_tracked")
            )
        )
        before = {k: v.clone() for k, v in net.state_dict().items() if "running_" in k}
        infer_all(net, [np.zeros((288, 512, 3), np.uint8)])
        self.assertTrue(net.training)
        self.assertTrue(
            all(torch.equal(v, net.state_dict()[k]) for k, v in before.items())
        )

    def test_miss_reject_wrong_all_remain_denominator(self):
        p = np.zeros((1, 5, 72, 128), np.float32)
        log = np.zeros((1, 4, 72, 128), np.float32)

        for cx in (40, 80):
            p[0, :, 35, cx] = [0.9, 40 / 512, 40 / 288, 0, 0]
        log[0, 2, 35, 40] = 10
        f = dict(
            id="x",
            objects=[
                dict(bbox=[140, 120, 180, 160], label=0),
                dict(bbox=[300, 120, 340, 160], label=1),
                dict(bbox=[400, 120, 440, 160], label=2),
            ],
        )
        c = dict(
            labels=["front_upright", "front_inverted", "back", "unreadable"],
            object_threshold=0.15,
            state_threshold=0.85,
        )
        r = score_frames([f], [[p, log]], c)
        self.assertEqual(
            (
                r["total"],
                r["detected"],
                r["wrong"],
                r["rejected"],
                r["missed"],
                r["correct"],
            ),
            (3, 2, 1, 1, 1, 0),
        )
        self.assertEqual(r["end_to_end_accuracy"], 0)

    def test_unreadable_miss_remains_in_class_recall(self):
        frame = dict(id="edge", objects=[dict(bbox=[0, 100, 30, 190], label=3)])
        cfg = dict(
            labels=["front_upright", "front_inverted", "back", "unreadable"],
            object_threshold=0.15,
            state_threshold=0.85,
        )
        r = score_frames([frame], [[]], cfg, decoded=True)
        self.assertEqual((r["total"], r["missed"], r["correct"]), (1, 1, 0))
        self.assertEqual(r["per_class"]["unreadable"]["missed"], 1)
        self.assertEqual(r["per_class"]["unreadable"]["recall"], 0)


if __name__ == "__main__":
    unittest.main()
