from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import copy
import io
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from console_model_deploy import (
    AUTH_ENV,
    AUTH_FILE,
    MODEL_FILES,
    _stop_guard,
    deploy_model,
    validate_plan,
)
from console_store import sha


class Writer(io.StringIO):
    def __init__(self, board, path):
        super().__init__()
        self.board = board
        self.path = path

    def __exit__(self, *args):
        self.board.files[self.path] = self.getvalue().encode()
        self.board.opened.append(self.path)
        for line in self.getvalue().splitlines():
            if line.startswith("Environment="):
                key, value = line.removeprefix("Environment=").split("=", 1)
                self.board.environment[key] = value
        return super().__exit__(*args)


class FakeBoard:
    def __init__(self, selection, source):
        self.pid = 111
        self.boot = "fixture-boot"
        self.remote = "/root/site"
        self.selection = selection
        self.source = source
        self.files = {}
        self.commands = []
        self.uploads = []
        self.opened = []
        self.atomic_paths = []
        self.started = False
        self.guard_failure = False
        self.environment = {
            "DISPLAY": ":0",
            "VISION_INFERENCE_CAPACITY": "3",
            "VISION_USE_OPTICAL_FLOW": "0",
            "VISION_MODEL_SELECTION_NONCE": "old-nonce",
            "VISION_MODEL_SELECTION_TICKET": "/root/old/selection.json",
        }
        self.s = SimpleNamespace(
            put=self.put, open=lambda path, mode: Writer(self, path)
        )

    def put(self, source, destination):
        self.files[destination] = Path(source).read_bytes()
        self.uploads.append(destination)

    def bytes(self, path):
        if path == "/proc/uptime":
            return b"1000.0 10.0"
        return self.files[path]

    def exists(self, path):
        return path in self.files

    def atomic(self, path, value):
        self.files[path] = json.dumps(value).encode()
        self.atomic_paths.append(path)

    def command(self, command, timeout=12):
        self.commands.append(command)
        if "&& systemctl stop " in command and self.guard_failure:
            raise RuntimeError(
                "Site config changed; service was not stopped: /root/site/demo_config.json"
            )
        if command.startswith("systemctl start "):
            self.started = True
        if command.startswith("systemctl show "):
            return b"222\n" if self.started else b"111\n"
        return b""

    def fresh(self, name):
        return dict(
            pid=222,
            model_sha256=self.selection["fine_sha256"],
            inference_enabled=True,
            recorded_monotonic=1001.0,
        )

    def snapshot(self):
        result = copy.deepcopy(self.source)
        result["pid"] = 222 if self.started else 111
        result["service"]["environment"] = dict(self.environment)
        return result


class ModelOnlyDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.local = Path(self.temp.name) / "run"
        self.local.mkdir()
        self.bundle = Path(self.temp.name) / "deployment"
        self.config = dict(
            labels=["pose-A", "pose-B"],
            titles=["A", "B"],
            input_size=[512, 288],
            input_transform="native_tiles_v1",
            mean=[123.675, 116.28, 103.53],
            std=[58.395, 57.12, 57.375],
            object_threshold=0.15,
            state_threshold=0.85,
            pixel_policy="native_crop_only_v1",
            window_stride=[256, 144],
            bbox_coordinates="native_tile_xyxy_plus_origin",
        )
        self.payloads = {
            "detector_fp16.rknn": b"fixture model: never loaded",
            "config.json": json.dumps(self.config).encode(),
        }
        for name, raw in self.payloads.items():
            (self.local / name).write_bytes(raw)
        hashes = {name: sha(raw) for name, raw in self.payloads.items()}
        self.selection = dict(
            hashes=hashes,
            fine_sha256=hashes["detector_fp16.rknn"],
            config_sha256=hashes["config.json"],
            training_started_beijing="2026-10-27T15:57:01+08:00",
            training_completed_beijing="2026-10-27T15:59:05+08:00",
            project_name="1027",
            run="chosen-fixture",
        )
        entry = "/root/site/deployments/original-live/console_entry.py"
        command = [
            "/root/venv/bin/python",
            "-u",
            entry,
            "--inference",
            "--monitors",
            "HDMI-1",
        ]
        settings = {
            "rectangle": [704, 396, 512, 288],
            "confidence_percent": 85,
            "track_hits": 3,
            "unrelated_fixture": {"keep": True},
        }
        self.before = dict(
            pid=111,
            root="/root/site",
            cwd="/root/site",
            entry=entry,
            command=command,
            search_path=["/root/site/deployments/original-live", "/root/site"],
            modules={
                name: dict(
                    path="/root/site/deployments/original-live/" + name + ".py",
                    sha256="1" * 64,
                )
                for name in (
                    "native_view",
                    "pose_chart",
                    "shared_preview",
                    "capture_scheduling",
                    "native_tracking",
                    "display_trace_async",
                    "full_roi_runtime",
                )
            },
            config_files={
                "/root/site/demo_config.json": dict(sha256="2" * 64, json=settings),
                "/root/site/saved_chart_settings.json": dict(
                    sha256="3" * 64, json={"mode": "pie", "interval": 5}
                ),
                "/root/site/image_controls.json": dict(
                    sha256="4" * 64, json={"rg": 376, "bg": 379}
                ),
            },
            state_files={"counter_state.json": {"counts": [100, 200]}},
            model=dict(is_symlink=True, target="/root/old-model", sha256="5" * 64),
            service=dict(
                exec_start=list(command),
                exec_start_definition=" ".join(command),
                working_directory="/root/site",
                environment={},
                unit_sha256="6" * 64,
            ),
        )
        self.board = FakeBoard(self.selection, self.before)
        self.before["service"]["environment"] = dict(self.board.environment)
        self.job = SimpleNamespace(
            check=MagicMock(), update=MagicMock(), protected=False
        )
        probe = patch(
            "console_model_deploy.snapshot_runtime",
            side_effect=lambda board: board.snapshot(),
        )
        self.probe = probe.start()
        self.addCleanup(probe.stop)
        self.plan = dict(
            kind="model_switch",
            files=self.selection["hashes"],
            metadata_files=["selection.json"],
            command=list(command),
            entry=entry,
            cwd="/root/site",
            module_changes={},
            config_changes={},
            model_config=self.config,
            transaction_directory="/root/site/model_versions/" + "a" * 32,
            model_link=dict(
                path="/root/site/model",
                target="/root/site/model_versions/" + "a" * 32 + "/model",
            ),
            environment_changes=dict(
                VISION_MODEL_SELECTION_NONCE="b" * 32,
                VISION_MODEL_SELECTION_TICKET="/root/site/model_versions/"
                + "a" * 32
                + "/model/selection.json",
            ),
        )

    def test_switch_keeps_actual_entry_modules_all_site_settings_and_model_bytes(self):
        result = deploy_model(
            self.board, self.local, self.selection, self.bundle, self.job
        )
        self.assertTrue(result["runtime_preserved"])
        self.assertTrue(result["site_config_preserved"])
        self.assertEqual(result["runtime_entry"], self.before["entry"])
        self.assertEqual({Path(path).name for path in self.board.uploads}, MODEL_FILES)
        self.assertEqual(len(self.board.uploads), 2)
        self.assertEqual(self.board.opened, [AUTH_FILE])
        authorization = self.board.files[AUTH_FILE].decode()
        self.assertNotIn("ExecStart", authorization)
        self.assertNotIn("WorkingDirectory", authorization)
        self.assertNotIn("VISION_INFERENCE_CAPACITY", authorization)
        for path in self.board.uploads:
            self.assertEqual(self.board.files[path], self.payloads[Path(path).name])
        self.assertEqual(
            self.board.snapshot()["config_files"], self.before["config_files"]
        )
        site = self.board.snapshot()["config_files"]["/root/site/demo_config.json"][
            "json"
        ]
        self.assertEqual(site["confidence_percent"], 85)
        self.assertEqual(site["track_hits"], 3)
        self.assertEqual(self.board.snapshot()["modules"], self.before["modules"])
        self.assertEqual(self.board.snapshot()["command"], self.before["command"])
        ticket = json.loads(self.board.files[self.board.atomic_paths[0]])
        self.assertTrue(ticket["fine_model_path"].endswith("/model/detector_fp16.rknn"))
        self.assertTrue(ticket["fine_config_path"].endswith("/model/config.json"))
        self.assertNotEqual(ticket["nonce"], "old-nonce")
        self.assertEqual(ticket["fine_sha256"], self.selection["fine_sha256"])
        self.assertFalse(
            any(
                "demo_config.json" in command
                and any(
                    operation in command
                    for operation in ("write_text", "write_bytes", "json.dump(")
                )
                for command in self.board.commands
            )
        )
        self.assertFalse(self.job.protected)

    def test_plan_blocks_runtime_file_before_any_stop(self):
        self.plan["files"] = dict(self.plan["files"], **{"native_view.py": "7" * 64})
        with self.assertRaisesRegex(ValueError, "白名单外文件：native_view.py"):
            validate_plan(self.before, self.plan)
        self.assertEqual(self.board.commands, [])

    def test_plan_blocks_entry_preview_tracking_and_site_configuration_changes(self):
        for field, value, expected in (
            ("entry", "/root/old/console_entry.py", "entry"),
            ("command", ["/root/venv/bin/python", "/root/old.py"], "command"),
            (
                "module_changes",
                {"shared_preview": "changed", "native_tracking": "changed"},
                "shared_preview",
            ),
            (
                "config_changes",
                {"demo_config.json.rectangle": [704, 396, 512, 288]},
                "demo_config.json.rectangle",
            ),
        ):
            plan = copy.deepcopy(self.plan)
            plan[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(
                ValueError, expected
            ):
                validate_plan(self.before, plan)
        self.assertEqual(self.board.commands, [])

    def test_model_config_cannot_smuggle_ui_or_site_settings(self):
        for field in ("ui_theme", "chart_interval", "rectangle", "track_hits"):
            plan = copy.deepcopy(self.plan)
            plan["model_config"][field] = "bad"
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, field):
                validate_plan(self.before, plan)

    def test_different_model_input_size_is_blocked_before_remote_upload_or_stop(self):
        self.config["input_size"] = [793, 317]
        raw = json.dumps(self.config).encode()
        (self.local / "config.json").write_bytes(raw)
        self.selection["hashes"]["config.json"] = sha(raw)
        self.selection["config_sha256"] = sha(raw)
        with self.assertRaisesRegex(
            ValueError, r"\[793, 317\].*\[512, 288\].*禁止修改 ROI"
        ):
            deploy_model(self.board, self.local, self.selection, self.bundle, self.job)
        self.assertEqual(self.board.uploads, [])
        self.assertEqual(self.board.commands, [])
        self.assertEqual(self.board.opened, [])
        self.assertEqual(self.board.atomic_paths, [])
        site = self.board.snapshot()["config_files"]["/root/site/demo_config.json"][
            "json"
        ]
        self.assertEqual(site["rectangle"], [704, 396, 512, 288])
        self.assertEqual((site["confidence_percent"], site["track_hits"]), (85, 3))

    def test_nonmodel_environment_changes_are_rejected(self):
        self.plan["environment_changes"]["VISION_INFERENCE_CAPACITY"] = "999"
        with self.assertRaisesRegex(ValueError, "VISION_INFERENCE_CAPACITY"):
            validate_plan(self.before, self.plan)

    def test_current_model_mutation_never_uploads_or_stops(self):
        (self.local / "detector_fp16.rknn").write_bytes(b"wrong model")
        with self.assertRaisesRegex(ValueError, "模型文件发生变化"):
            deploy_model(self.board, self.local, self.selection, self.bundle, self.job)
        self.assertEqual(self.board.uploads, [])
        self.assertEqual(self.board.commands, [])

    def test_conflicting_choice_identity_is_rejected_before_upload(self):
        self.selection["fine_sha256"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "身份哈希不一致"):
            deploy_model(self.board, self.local, self.selection, self.bundle, self.job)
        self.assertEqual(self.board.uploads, [])
        self.assertEqual(self.board.commands, [])

    def test_runtime_or_site_change_during_upload_blocks_service_stop(self):
        for field in ("modules", "config_files", "command"):
            before = self.board.snapshot()
            changed = copy.deepcopy(before)
            if field == "command":
                changed[field] = before[field] + ["--different-entry-arguments"]
            else:
                changed[field] = dict(changed[field], changed={"sha256": "9" * 64})
            self.probe.side_effect = [before, changed]
            self.board.commands.clear()
            self.board.uploads.clear()
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, field):
                deploy_model(
                    self.board, self.local, self.selection, self.bundle, self.job
                )
            self.assertFalse(
                any("systemctl stop" in command for command in self.board.commands)
            )
            self.assertEqual(self.board.opened, [])
            self.assertEqual(self.board.atomic_paths, [])

    def test_authorization_file_cannot_erase_a_runtime_override(self):
        self.board.files[AUTH_FILE] = b"[Service]\nExecStart=/root/keep.py\n"
        with self.assertRaisesRegex(ValueError, "ExecStart"):
            deploy_model(self.board, self.local, self.selection, self.bundle, self.job)
        self.assertEqual(self.board.commands, [])
        self.assertEqual(self.board.opened, [])

    def test_final_config_guard_failure_does_not_commit_or_start(self):
        self.board.guard_failure = True
        with self.assertRaisesRegex(ValueError, "demo_config.json"):
            deploy_model(self.board, self.local, self.selection, self.bundle, self.job)
        self.assertFalse(self.board.started)
        self.assertEqual(self.board.opened, [])
        self.assertFalse(
            any(".model-link-" in command for command in self.board.commands)
        )
        self.assertFalse(self.job.protected)
        record = json.loads(
            (self.bundle / "preflight.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            (record["state"], record["phase"]), ("failed", "final_stop_guard")
        )
        compile(_stop_guard(self.before), "final_model_stop_guard", "exec")

    def test_changed_poststart_ui_is_reported_instead_of_claiming_preservation(self):
        before = self.board.snapshot()
        changed = copy.deepcopy(before)
        changed["modules"]["native_view"]["sha256"] = "e" * 64
        changed["pid"] = 222
        self.probe.side_effect = [before, before, changed]
        with self.assertRaisesRegex(ValueError, "modules.native_view"):
            deploy_model(self.board, self.local, self.selection, self.bundle, self.job)
        self.assertFalse((self.bundle / "result.json").exists())
        self.assertFalse(self.job.protected)

    def test_restart_can_advance_isp_revision_without_changing_site_parameters(self):
        path = "/root/site/image_controls.json"
        self.before["config_files"][path]["json"] = dict(
            config=dict(rg=376, bg=379, brightness=-15, revision=1052),
            auto=dict(wb=False, brightness=False, contrast=False),
        )
        original = self.board.snapshot

        def restarted(board):
            value = original()
            if board.started:
                record = value["config_files"][path]
                record["json"]["config"]["revision"] += 2
                record["sha256"] = sha(json.dumps(record["json"]).encode())
            return value

        self.probe.side_effect = restarted
        result = deploy_model(
            self.board, self.local, self.selection, self.bundle, self.job
        )
        self.assertTrue(result["site_config_preserved"])
        self.assertTrue(result["runtime_preserved"])
        self.assertEqual(
            self.board.snapshot()["config_files"][path]["json"]["config"]["revision"],
            1052,
        )

    def test_restart_revision_does_not_hide_changed_brightness(self):
        path = "/root/site/image_controls.json"
        self.before["config_files"][path]["json"] = dict(
            config=dict(rg=376, bg=379, brightness=-15, revision=1052),
            auto=dict(wb=False, brightness=False, contrast=False),
        )
        original = self.board.snapshot

        def restarted(board):
            value = original()
            if board.started:
                record = value["config_files"][path]
                record["json"]["config"].update(revision=1054, brightness=-14)
                record["sha256"] = sha(json.dumps(record["json"]).encode())
            return value

        self.probe.side_effect = restarted
        with self.assertRaisesRegex(ValueError, r"image_controls\.json\.config"):
            deploy_model(self.board, self.local, self.selection, self.bundle, self.job)
        self.assertFalse((self.bundle / "result.json").exists())
        self.assertFalse(self.job.protected)


if __name__ == "__main__":
    unittest.main()
