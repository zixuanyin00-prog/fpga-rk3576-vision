from collections import deque
from pathlib import Path
from types import SimpleNamespace
import json
import sys
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "evidence" / "model_only_repair_20261027" / "baseline"


with patch.object(sys, "path", [str(BASELINE), str(ROOT), *sys.path]):
    import model_input_adapter as adapter
    from ordered_whole_frames import OrderedWholeFrames as OriginalScheduler
    from native_windows import plan
    import native_outputs
    import native_decode


class ModelInputAdapterTests(unittest.TestCase):
    def setUp(self):
        self.rect = [704, 396, 512, 288]
        self.config = dict(
            input_transform="native_tiles_v1",
            input_size=[512, 288],
            window_stride=[256, 144],
            labels=["A", "B", "C"],
            object_threshold=0.15,
        )
        adapter.validate_model_config(self.config)
        yy, xx = np.indices((288, 512))
        self.rgb = np.stack((xx % 256, yy % 256, xx // 256), axis=2).astype(np.uint8)
        now = time.monotonic()
        self.frame = SimpleNamespace(
            x=704,
            y=396,
            rgb=self.rgb,
            source_frame_id=9123,
            capture_received_monotonic=now - 0.01,
            transport_dequeued_monotonic=now - 0.009,
            python_copy_returned_monotonic=now - 0.008,
        )
        self.job = dict(
            frame=self.frame,
            config=dict(
                rectangle=self.rect.copy(), confidence_percent=85, track_hits=1
            ),
            acquired_monotonic=now - 0.007,
            scheduler_admitted_monotonic=now - 0.006,
            npu_worker_started_monotonic=now - 0.005,
        )
        self.inputs = []
        tiles = plan(self.rect)

        def inference(inputs, data_format):
            self.assertEqual(data_format, ["nhwc"])
            inp = inputs[0]
            self.assertEqual(inp.dtype, np.uint8)
            self.assertEqual(inp.shape, (1, 288, 512, 3))
            self.assertTrue(inp.flags.c_contiguous)
            self.inputs.append(inp.copy())
            tile = tiles[len(self.inputs) - 1]

            center_x, center_y = 916 - tile[0], 512 - tile[1]
            gx, gy = int(center_x // 4), int(center_y // 4)
            p = np.zeros((1, 5, 72, 128), dtype=np.float32)
            logits = np.zeros((1, 3, 72, 128), dtype=np.float32)
            p[0, :, gy, gx] = [
                0.9,
                32 / 512,
                24 / 288,
                center_x / 4 - gx,
                center_y / 4 - gy,
            ]
            logits[0, 0, gy, gx] = 9
            return [p, logits]

        self.engine = SimpleNamespace(inference=inference)
        self.pipeline = SimpleNamespace(
            config=self.config,
            engines=[self.engine, self.engine],
            sample_lock=threading.Lock(),
            core_samples=[deque(), deque()],
            core_calls=[0, 0],
            stale_results=0,
            _valid=lambda job: True,
        )

    def test_fixed_model_roi_uses_exact_same_source_pixels_and_global_coordinates(self):
        original = self.rgb.copy()
        objects, sample = adapter.infer(self.pipeline, 0, self.job)
        expected = plan(self.rect)
        self.assertEqual(len(expected), 1)
        coverage = np.zeros((288, 512), dtype=bool)
        for actual, (x, y, w, h) in zip(self.inputs, expected):
            dx, dy = x - self.rect[0], y - self.rect[1]
            np.testing.assert_array_equal(actual[0], original[dy : dy + h, dx : dx + w])
            coverage[dy : dy + h, dx : dx + w] = True
        self.assertTrue(coverage.all())
        np.testing.assert_array_equal(self.rgb, original)
        self.assertEqual(self.job["config"]["rectangle"], self.rect)
        self.assertEqual(
            adapter.capture_rectangle(self.job["config"], self.config), self.rect
        )
        self.assertEqual(sample["native_capture_rectangle"], self.rect)
        self.assertEqual(sample["native_tiles"], [list(tile) for tile in expected])
        self.assertEqual(len(objects), 1)
        np.testing.assert_allclose(objects[0]["bbox"], [900, 500, 932, 524], atol=0.001)
        self.assertEqual(objects[0]["raw_label"], "A")
        self.assertGreater(objects[0]["confidence"], 0.99)

    def test_one_model_call_is_one_complete_source_result_with_original_timing_fields(
        self,
    ):
        _, sample = adapter.infer(self.pipeline, 1, self.job)
        self.assertEqual(self.pipeline.core_calls, [0, 1])
        self.assertEqual(len(self.pipeline.core_samples[1]), 1)
        self.assertEqual(sample["model_calls"], 1)
        self.assertEqual(sample["tile_calls"], 1)
        self.assertTrue(sample["complete_native_roi"])
        self.assertEqual(sample["source_frame_id"], self.frame.source_frame_id)
        fields = (
            "queue_wait_ms",
            "native_ring_wait_ms",
            "admitted_to_worker_ms",
            "preprocessing_ms",
            "input_copy_ms",
            "decode_ms",
            "rknn_ms",
            "received_to_result_ms",
        )
        for row in self.pipeline.core_samples[1]:
            self.assertTrue(set(fields).issubset(row))
            self.assertEqual(row["model_calls"], 1)
            self.assertFalse(row["complete_native_roi"])
            self.assertEqual(row["source_frame_id"], self.frame.source_frame_id)
        self.assertLessEqual(
            sample["npu_started_monotonic"], sample["npu_finished_monotonic"]
        )
        self.assertLessEqual(
            sample["npu_finished_monotonic"], sample["decode_finished_monotonic"]
        )
        self.assertEqual(
            sample["transport_received_monotonic"],
            self.frame.capture_received_monotonic,
        )

    def test_invalidated_frame_is_rejected_before_a_model_call(self):
        self.pipeline._valid = lambda job: False
        result = adapter.infer(self.pipeline, 0, self.job)
        self.assertIsNone(result)
        self.assertEqual(len(self.inputs), 0)
        self.assertEqual(self.pipeline.core_calls, [0, 0])
        self.assertEqual(self.pipeline.stale_results, 1)

    def test_wrong_capture_or_pixel_type_is_rejected_before_model_execution(self):
        for field, wrong in [
            ("x", 705),
            ("rgb", self.rgb.astype(np.float32)),
            ("rgb", self.rgb[:287]),
        ]:
            original = getattr(self.frame, field)
            setattr(self.frame, field, wrong)
            with self.subTest(field=field), self.assertRaisesRegex(
                ValueError, "complete native capture"
            ):
                adapter.infer(self.pipeline, 0, self.job)
            setattr(self.frame, field, original)
        self.assertEqual(self.inputs, [])

    def test_unknown_model_contract_is_rejected_before_loading(self):
        for change in (
            dict(input_transform="stretch_bilinear"),
            dict(input_size=[793, 317]),
            dict(window_stride=[512, 288]),
            dict(labels=["same", "same"]),
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                adapter.validate_model_config(dict(self.config, **change))

    def test_actual_selected_training_config_needs_no_rewrite(self):
        path = (
            ROOT
            / "console_data"
            / "f0d2fef161a84a2e82c3141ebc26887a"
            / "runs"
            / "c27e76471e8b4c1d9d7e368272a2aef4"
            / "config.json"
        )
        raw = path.read_bytes()
        config = json.loads(raw)
        self.assertIs(adapter.validate_model_config(config), config)
        self.assertEqual(path.read_bytes(), raw)

    def test_raw_single_head_and_probability_pair_have_the_same_native_geometry(self):
        original = self.engine.inference

        def raw_inference(inputs, data_format):
            p, logits = original(inputs, data_format)
            safe = np.clip(p, 1e-6, 1 - 1e-6)
            return [np.concatenate((np.log(safe / (1 - safe)), logits), axis=1)]

        self.engine.inference = raw_inference
        objects, sample = adapter.infer(self.pipeline, 0, self.job)
        self.assertEqual(len(objects), 1)
        np.testing.assert_allclose(objects[0]["bbox"], [900, 500, 932, 524], atol=0.001)
        self.assertEqual(sample["model_calls"], 1)

    def test_wrong_output_grid_is_refused_before_a_complete_result_can_be_published(
        self,
    ):
        self.engine.inference = lambda inputs, data_format: [
            np.zeros((1, 5, 71, 128), dtype=np.float32),
            np.zeros((1, 3, 71, 128), dtype=np.float32),
        ]
        with self.assertRaisesRegex(ValueError, "native 72x128"):
            adapter.infer(self.pipeline, 0, self.job)

    def test_current_control_position_can_move_while_model_size_stays_fixed(self):
        def no_detections(inputs, data_format):
            self.assertEqual(inputs[0].dtype, np.uint8)
            self.assertEqual(inputs[0].shape, (1, 288, 512, 3))
            self.inputs.append(inputs[0].copy())
            return [
                np.zeros((1, 5, 72, 128), dtype=np.float32),
                np.zeros((1, 3, 72, 128), dtype=np.float32),
            ]

        self.engine.inference = no_detections
        for rect in (
            [704, 396, 512, 288],
            [575, 375, 512, 288],
            [0, 0, 512, 288],
            [1408, 792, 512, 288],
        ):
            self.inputs.clear()
            self.job["config"]["rectangle"] = rect.copy()
            self.frame.x, self.frame.y = rect[:2]
            self.frame.rgb = np.zeros((rect[3], rect[2], 3), dtype=np.uint8)
            with self.subTest(rectangle=rect):
                _, sample = adapter.infer(self.pipeline, 0, self.job)
                self.assertEqual(sample["native_capture_rectangle"], rect)
                self.assertEqual(sample["model_calls"], 1)
                self.assertEqual(len(self.inputs), 1)
                self.assertEqual(self.job["config"]["rectangle"], rect)
                self.assertEqual(
                    adapter.capture_rectangle(self.job["config"], self.config), rect
                )

    def test_current_confidence_controls_classification_without_rewriting_control_settings(
        self,
    ):
        def moderate_confidence(inputs, data_format):
            p = np.zeros((1, 5, 72, 128), dtype=np.float32)
            logits = np.zeros((1, 3, 72, 128), dtype=np.float32)
            p[0, :, 25, 32] = [0.9, 32 / 512, 24 / 288, 0.0, 0.0]
            logits[0, 0, 25, 32] = 2.0
            return [p, logits]

        self.engine.inference = moderate_confidence
        self.job["config"]["rectangle"] = [704, 396, 512, 288]
        self.frame.x, self.frame.y = 704, 396
        self.frame.rgb = np.zeros((288, 512, 3), dtype=np.uint8)
        for confidence, hits, label in ((60, 1, "A"), (85, 3, "uncertain")):
            self.job["config"].update(confidence_percent=confidence, track_hits=hits)
            before = dict(self.job["config"])
            objects, sample = adapter.infer(self.pipeline, 0, self.job)
            self.assertEqual(objects[0]["label"], label)
            self.assertGreater(objects[0]["confidence"], 0.78)
            self.assertLess(objects[0]["confidence"], 0.79)
            self.assertEqual(self.job["config"], before)
            self.assertEqual(sample["model_calls"], 1)

    def test_every_non_model_size_is_refused_without_resize_padding_or_multi_window_fallback(
        self,
    ):
        for rect in (
            [575, 375, 793, 317],
            [620, 390, 600, 300],
            [704, 396, 513, 288],
            [704, 396, 512, 289],
            [704, 396, 511, 288],
            [704, 396, 512, 287],
        ):
            self.job["config"]["rectangle"] = rect
            with self.subTest(rectangle=rect), self.assertRaisesRegex(
                ValueError, "512"
            ):
                adapter.capture_rectangle(self.job["config"], self.config)
            with self.subTest(rectangle=rect), self.assertRaisesRegex(
                ValueError, "512"
            ):
                adapter.infer(self.pipeline, 0, self.job)
        self.assertEqual(self.inputs, [])

    def test_only_scheduler_snapshot_is_adapted_and_original_scheduling_methods_are_inherited(
        self,
    ):
        self.assertEqual(
            set(adapter.OrderedWholeFrames.__dict__) - {"__module__", "__doc__"},
            {"snapshot"},
        )
        for method in (
            "__init__",
            "start",
            "submit",
            "_worker",
            "_collect",
            "invalidate",
            "stop",
            "close",
            "call_control",
            "is_collector_thread",
            "timing_snapshot",
        ):
            self.assertIs(
                getattr(adapter.OrderedWholeFrames, method),
                getattr(OriginalScheduler, method),
            )

        class Pipeline:
            config = self.config
            settings = dict(rectangle=self.rect.copy())

            def execute(self, core, job):
                raise AssertionError("Tests never start an execution worker")

        pipeline = Pipeline()
        scheduler = adapter.OrderedWholeFrames(
            pipeline.execute, lambda job, result: None, lambda error: None, capacity=3
        )
        info = scheduler.snapshot()
        self.assertEqual(info["model_calls_per_completed_source"], 1)
        self.assertEqual(info["tiles_per_complete_source"], 1)
        self.assertEqual(info["input_transform"], "native_tiles_v1")
        self.assertEqual(scheduler.capacity, 3)
        self.assertEqual(len(scheduler.threads), 3)
        pipeline.config = dict(input_transform="native_full_roi_fpn_integer_crop_v1")
        original = OriginalScheduler(
            pipeline.execute, lambda job, result: None, lambda error: None, capacity=3
        )

        self.assertEqual(
            scheduler.snapshot()["input_transform"],
            original.snapshot()["input_transform"],
        )
        self.assertEqual(scheduler.snapshot()["model_calls_per_completed_source"], 1)
        scheduler.close()
        original.close()


if __name__ == "__main__":
    unittest.main()
