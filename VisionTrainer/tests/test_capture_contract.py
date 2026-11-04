import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from core import store as db
from service import Service, DEFAULTS
from capture_simulator import CaptureSimulator, SimulatedClient, PROTOCOL


class CaptureTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name, value in [
            ("ROOT", self.root / "app"),
            ("DATA", self.root / "projects"),
        ]:
            mock = patch.object(db, name, value)
            mock.start()
            self.addCleanup(mock.stop)

        self.settings = copy.deepcopy(DEFAULTS)
        self.settings.update(
            host="simulator.invalid",
            username="simulator",
            key="unused",
            auto_capture=False,
            frames=15,
            stride=1,
        )
        db.write(db.ROOT / "settings.json", self.settings)
        network = patch.object(
            Service,
            "connect",
            side_effect=AssertionError("real SSH forbidden in protocol tests"),
        )
        network.start()
        self.addCleanup(network.stop)

    def simulator(self, **kwargs):
        root = self.root / ("board_" + str(len(list(self.root.glob("board_*")))))
        root.mkdir()
        sim = CaptureSimulator(root, self.settings["remote"], **kwargs)
        self.addCleanup(sim.close)
        return sim

    def service(self):
        s = Service()
        s.job = {}
        return s

    def project(self, sim):
        p = db.create("isolated simulated scene")
        p["calibration"] = dict(
            confirmed=True,
            origin=sim.point,
            host=self.settings["host"],
            remote=self.settings["remote"],
            boot_id=sim.boot_id,
            probe_id="simulated_camera_position",
        )
        p["environment"] = dict(approved=copy.deepcopy(sim.profile))
        db.save(p)
        return p

    def capture(self, s, p, sim, role="train", batch=None):
        with patch.object(
            s, "connect", return_value=SimulatedClient(sim)
        ), patch.object(s, "suggest_action") as suggestions:
            rows = s.capture_action(p["id"], role, batch)

        if role == "train" and rows:
            suggestions.assert_called_once()
        else:
            suggestions.assert_not_called()
        return rows

    def test_board_reboot_invalidates_geometry_and_optical_confirmation(self):
        sim = self.simulator()
        p = self.project(sim)
        p["calibration"]["boot_id"] = "previous_boot"
        db.save(p)
        with self.assertRaisesRegex(ValueError, "启动已改变"):
            self.capture(self.service(), p, sim)
        self.assertFalse(sim.probes)
        self.assertFalse(sim.requests)
        self.assertNotIn("calibration", db.load(p["id"]))
        self.assertEqual(db.load(p["id"])["frames"], [])

    def test_native_capture_integrity_and_no_fps_claim(self):
        sim = self.simulator()
        p = self.project(sim)
        rows = self.capture(self.service(), p, sim)
        self.assertEqual(len(rows), 15)
        self.assertEqual(len({f["source_frame_id"] for f in rows}), 15)
        self.assertTrue(
            all(
                f["origin"] == sim.point and f["native_roi"] and not f["reviewed"]
                for f in rows
            )
        )
        self.assertEqual(sim.probes[0]["kind"], "region")
        self.assertEqual(sim.probes[0]["origin"], sim.point)
        request = sim.requests[0]
        self.assertEqual(request["protocol"], PROTOCOL)
        self.assertEqual(request["frames"], 15)
        self.assertEqual(request["max_attempts"], 45)
        self.assertEqual(request["native_roi"], sim.point + [512, 288])
        self.assertEqual(
            request["environment_profile_digest"], sim.profile["profile_digest"]
        )
        self.assertEqual(
            request["expected_sensor_generation"], sim.sensor["sensor_generation"]
        )
        self.assertEqual(request["sensor_stable_source_id"], 100)
        self.assertEqual(request["exclude_source_frame_ids"], [])
        root = db.project_path(p["id"])
        for f in rows:
            self.assertEqual(
                hashlib.sha256((root / f["file"]).read_bytes()).hexdigest(), f["sha256"]
            )
        status = db.read(next(root.glob("captures/*/*/capture_status.json")))
        self.assertEqual(status["state"], "complete")
        self.assertEqual(len(status["files"]), 15)
        evidence = db.read(next(root.glob("captures/*/*/capture_evidence.json")))
        self.assertFalse(evidence["display_60fps_verified"])
        self.assertFalse(evidence["display"]["verified"])
        self.assertEqual(set(evidence["display"]["displays"]), {"HDMI-1", "DSI-1"})

    def test_held_out_capture_requests_ten_without_auto_suggestions(self):
        sim = self.simulator()
        p = self.project(sim)
        rows = self.capture(self.service(), p, sim, "test")
        self.assertEqual(len(rows), 10)
        request = sim.requests[0]
        self.assertEqual(request["max_attempts"], 30)
        self.assertTrue(request["no_training"])
        self.assertEqual(request["role"], "held_out_dynamic_validation")
        self.assertTrue(all(f["role"] == "test" for f in rows))

    def test_downloads_use_explicit_file_list_after_rejected_attempts(self):
        sim = self.simulator(faults={"reject_first_two"})
        p = self.project(sim)
        rows = self.capture(self.service(), p, sim)
        status = sim.settle_status(sim.requests[0]["id"])
        self.assertEqual(status["state"], "complete")
        self.assertEqual(status["attempts"], 17)
        self.assertEqual(status["files"], [f"{i:04}" for i in range(3, 18)])
        downloads = [
            Path(path).stem
            for path in sim.downloads
            if "/dynamic_audit/" in path and path.endswith(".rgb")
        ]
        self.assertEqual(downloads, status["files"])
        self.assertEqual(len(rows), 15)

    def test_cancelled_backend_preserves_only_valid_unreviewed_originals(self):
        sim = self.simulator(faults={"cancel_after_first"})
        p = self.project(sim)
        rows = self.capture(self.service(), p, sim)
        status = sim.settle_status(sim.requests[0]["id"])
        self.assertEqual(status["state"], "cancelled")
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["reviewed"])
        self.assertFalse(db.training_ready(db.load(p["id"]))[0])

    def test_duplicate_source_is_rejected_by_backend_and_batch_exhausts(self):
        sim = self.simulator(faults={"duplicate_source"})
        p = self.project(sim)
        rows = self.capture(self.service(), p, sim)
        self.assertEqual(len(rows), 1)
        status = sim.settle_status(sim.requests[0]["id"])
        self.assertEqual(status["state"], "exhausted")
        self.assertEqual(status["attempts"], 45)
        self.assertEqual(len(status["files"]), 1)
        self.assertEqual(status["rejections"][0]["reason"], "duplicate_source_frame_id")
        self.assertFalse(db.training_ready(db.load(p["id"]))[0])

    def test_identical_pixels_are_not_new_originals(self):
        sim = self.simulator(faults={"duplicate_pixels"})
        p = self.project(sim)
        rows = self.capture(self.service(), p, sim)
        self.assertEqual(len(rows), 1)
        status = sim.settle_status(sim.requests[0]["id"])
        self.assertEqual(status["state"], "exhausted")
        self.assertEqual(status["attempts"], 45)
        self.assertIn("duplicate_image_sha256", status["rejections"][0]["error"])

    def test_source_before_stable_fence_never_enters_dataset(self):
        sim = self.simulator(faults={"before_stable_fence"})
        p = self.project(sim)
        self.assertEqual(self.capture(self.service(), p, sim), [])
        status = sim.settle_status(sim.requests[0]["id"])
        self.assertEqual(status["state"], "exhausted")
        self.assertTrue(
            all(
                r["reason"] == "source_not_after_stable_fence"
                for r in status["rejections"]
            )
        )
        self.assertEqual(db.load(p["id"])["frames"], [])

    def test_wrong_region_is_rejected_by_backend(self):
        sim = self.simulator(faults={"wrong_frame_region"})
        p = self.project(sim)
        self.assertEqual(self.capture(self.service(), p, sim), [])
        status = sim.settle_status(sim.requests[0]["id"])
        self.assertEqual(status["state"], "exhausted")
        self.assertTrue(
            all(r["reason"] == "native_roi_mismatch" for r in status["rejections"])
        )

    def test_corrupt_downloads_block_entire_dataset_commit(self):
        for fault in (
            "corrupt_download_region",
            "corrupt_download_duplicate",
            "corrupt_download_size",
        ):
            with self.subTest(fault=fault):
                sim = self.simulator(faults={fault})
                p = self.project(sim)
                with self.assertRaisesRegex(
                    ValueError, "错误尺寸、重复源帧或采集区域变化"
                ):
                    self.capture(self.service(), p, sim)
                self.assertEqual(db.load(p["id"])["frames"], [])

    def test_missing_or_mismatched_protocol_never_submits_request(self):
        for metadata in (None, dict(protocol="legacy-written")):
            with self.subTest(metadata=metadata):
                sim = self.simulator()
                p = self.project(sim)
                if metadata is None:
                    (sim.audit.root / "protocol.json").unlink()
                else:
                    (sim.audit.root / "protocol.json").write_text(json.dumps(metadata))
                with self.assertRaisesRegex(ValueError, "v15采样后端|协议不匹配"):
                    self.capture(self.service(), p, sim)
                self.assertFalse(sim.requests)
                self.assertEqual(db.load(p["id"])["frames"], [])

    def test_mismatched_response_protocol_blocks_download_and_commit(self):
        sim = self.simulator(faults={"bad_response_protocol"})
        p = self.project(sim)
        with self.assertRaisesRegex(ValueError, "返回采样协议不匹配"):
            self.capture(self.service(), p, sim)
        self.assertFalse(any("/dynamic_audit/" in path for path in sim.downloads))
        self.assertEqual(db.load(p["id"])["frames"], [])

    def test_busy_or_queued_backend_is_not_overwritten(self):
        for state in ("running", "pending", "queued"):
            with self.subTest(state=state):
                sim = self.simulator()
                p = self.project(sim)
                if state == "queued":
                    sim.files[sim.remote + "/dynamic_audit/request.json"] = (
                        b'{"owner":"another-session"}'
                    )
                else:
                    sim.files[sim.remote + "/dynamic_audit/active.json"] = (
                        sim.json_bytes(
                            dict(
                                state="running" if state == "running" else "complete",
                                pending=1 if state == "pending" else 0,
                            )
                        )
                    )
                with self.assertRaisesRegex(
                    ValueError, "其他采样事务|已有等待采集请求"
                ):
                    self.capture(self.service(), p, sim)
                self.assertFalse(sim.requests)
                self.assertEqual(db.load(p["id"])["frames"], [])
                if state == "queued":
                    self.assertEqual(
                        sim.files[sim.remote + "/dynamic_audit/request.json"],
                        b'{"owner":"another-session"}',
                    )

    def test_environment_change_or_service_restart_blocks_commit(self):
        for fault, message in [
            ("sensor_change_after_capture", "传感器代际已改变"),
            ("restart_after_capture", "服务重启"),
        ]:
            with self.subTest(fault=fault):
                sim = self.simulator(faults={fault})
                p = self.project(sim)
                with self.assertRaisesRegex(ValueError, message):
                    self.capture(self.service(), p, sim)
                self.assertEqual(db.load(p["id"])["frames"], [])

    def test_missing_fixed_scene_approval_blocks_request(self):
        sim = self.simulator()
        p = self.project(sim)
        p.pop("environment")
        db.save(p)
        with self.assertRaisesRegex(ValueError, "确认当前固定场景配置"):
            self.capture(self.service(), p, sim)
        self.assertFalse(sim.requests)

    def test_refill_requests_only_missing_originals_and_excludes_prior_sources(self):
        sim = self.simulator()
        p = self.project(sim)
        s = self.service()
        rows = self.capture(s, p, sim)
        p = db.load(p["id"])
        batch = rows[0]["batch"]
        p["frames"] = p["frames"][:12]
        for f in p["frames"]:
            f.update(
                reviewed=True,
                quality_review="pass",
                all_objects_reviewed=True,
                objects=[],
            )
        db.save(p)
        prior = copy.deepcopy(p["frames"])
        new = self.capture(s, p, sim, batch=batch)
        request = sim.requests[-1]
        self.assertEqual(request["frames"], 3)
        self.assertEqual(request["max_attempts"], 9)
        self.assertEqual(
            request["exclude_source_frame_ids"], [f["source_frame_id"] for f in prior]
        )
        self.assertEqual(request["exclude_image_sha256"], [f["sha256"] for f in prior])
        fresh = db.load(p["id"])
        self.assertEqual(len(fresh["frames"]), 15)
        self.assertEqual(fresh["frames"][:12], prior)
        self.assertTrue(all(f["batch"] == batch and not f["reviewed"] for f in new))

    def test_unrefined_or_full_batch_cannot_silently_expand(self):
        sim = self.simulator()
        p = self.project(sim)
        s = self.service()
        rows = self.capture(s, p, sim)
        batch = rows[0]["batch"]
        with self.assertRaisesRegex(ValueError, "先逐张完成"):
            self.capture(s, p, sim, batch=batch)
        p = db.load(p["id"])
        for f in p["frames"]:
            f.update(reviewed=True, quality_review="pass", all_objects_reviewed=True)
        db.save(p)
        with self.assertRaisesRegex(ValueError, "已足15张"):
            self.capture(s, p, sim, batch=batch)
        self.assertEqual(len(sim.requests), 1)

    def test_new_scene_probes_instead_of_blind_capture(self):
        p = db.create("new camera")
        s = self.service()
        with patch.object(s, "probe") as probe, patch.object(s, "launch") as bulk:
            s.capture(p["id"], "train")
            probe.assert_called_once_with(p["id"])
            bulk.assert_not_called()
        with self.assertRaisesRegex(ValueError, "清晰度"):
            s.capture_action(p["id"], "train")

    def test_stale_probe_confirmation_and_changed_host_rejected(self):
        p = db.create("stale")
        p["probe"] = dict(
            id="new",
            ok=True,
            origin=[800, 470],
            host=self.settings["host"],
            remote=self.settings["remote"],
        )
        db.save(p)
        s = self.service()
        s.job = None
        with self.assertRaises(ValueError):
            s.confirm_region(p["id"], "old")
        s.confirm_region(p["id"], "new")
        self.assertTrue(s.calibrated(db.load(p["id"])))
        s.settings["host"] = "different"
        self.assertFalse(s.calibrated(db.load(p["id"])))
        with self.assertRaises(ValueError):
            s.confirm_region(p["id"], "new")

    def test_activation_once_and_offline_then_normal_launch(self):
        s = self.service()
        s.job = None
        s.settings["auto_capture"] = True
        with patch.object(s, "probe") as probe:
            self.assertIsNone(s.startup(False, "offline"))
            self.assertEqual(db.listing(), [])
            id = s.startup(True, "launch1")
            self.assertIsNotNone(id)
            probe.assert_called_once_with(id)
            self.assertIsNone(s.startup(True, "launch1"))
            self.assertEqual(probe.call_count, 1)
            second = s.startup(True, "launch2")
            self.assertNotEqual(id, second)
            self.assertEqual(probe.call_count, 2)
        self.assertIsNone(s.box_selection)

    def test_automatic_sampling_disabled_in_config_creates_nothing(self):
        s = self.service()
        s.job = None
        with patch.object(s, "probe") as probe:
            self.assertFalse(s.settings["auto_capture"])
            self.assertIsNone(s.startup(True, "disabled"))
            probe.assert_not_called()
            self.assertEqual(db.listing(), [])

    def test_sampling_configuration_is_fifteen_originals_and_no_stride(self):
        s = self.service()
        s.job = None
        self.assertEqual((s.settings["frames"], s.settings["stride"]), (15, 1))
        for values in [dict(frames=3), dict(frames=16), dict(stride=2)]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                s.configure(values)
        s.configure(dict(frames=15, stride=1, auto_capture=True))
        self.assertTrue(db.read(db.ROOT / "settings.json")["auto_capture"])


if __name__ == "__main__":
    unittest.main()
