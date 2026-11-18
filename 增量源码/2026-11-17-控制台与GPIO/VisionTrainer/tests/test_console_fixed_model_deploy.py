from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import contextlib
import copy
import io
import json
import shlex
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import console_model_deploy as deploy
from console_store import sha


class Writer(io.BytesIO):
    def __init__(self, board, path):
        super().__init__()
        self.board = board
        self.path = path

    def __exit__(self, *args):
        self.board.files[self.path] = self.getvalue()
        self.board.opened.append(self.path)
        return super().__exit__(*args)


class FixedBoard:
    def __init__(self, source, camera, manifest):
        self.remote = "/root/site"
        self.pid = 111
        self.boot = "fixture-boot"
        self.source = source
        self.camera = camera
        self.started = False
        self.path = self.remote + "/flash_boot/release.json"
        self.command_args = [
            "/usr/bin/python3",
            self.remote + "/flash_boot/flash_boot.py",
            "--manifest",
            self.path,
        ]
        self.definition = (
            "{ path=/usr/bin/python3 ; argv[]="
            + shlex.join(self.command_args)
            + " ; ignore_errors=no ; pid=111 }"
        )
        self.files = {self.path: json.dumps(manifest).encode()}
        self.commands = []
        self.uploads = []
        self.opened = []
        self.atomic_paths = []
        self.s = SimpleNamespace(
            put=self.put,
            open=lambda path, mode: Writer(self, path),
            posix_rename=self.rename,
        )

    def put(self, source, target):
        self.files[target] = Path(source).read_bytes()
        self.uploads.append(target)

    def rename(self, source, target):
        self.files[target] = self.files.pop(source)

    def bytes(self, path):
        return self.files[path]

    def exists(self, path):
        return path in self.files

    def atomic(self, path, value):
        self.files[path] = json.dumps(value).encode()
        self.atomic_paths.append(path)

    def command(self, command, timeout=12):
        self.commands.append(command)
        if command == "systemctl show " + deploy.SERVICE + " -p ExecStart --value":
            return self.definition.encode()
        if command.startswith("systemctl start "):
            self.started = True
        if command.startswith("systemctl show "):
            return b"222\n" if self.started else b"111\n"
        if "print(json.dumps(result))" in command:
            return b'{"archived": true, "path": "fixture-history"}'
        return b""

    def fresh(self, name):
        if name == "camera_console/status.json":
            camera = copy.deepcopy(self.camera)
            if self.started:
                camera["automatic"]["enhancement"]["revision"] += 1
                camera["automatic"]["enhancement"]["effective_revision"] += 1
            return camera
        selected = json.loads(self.files[self.path])["selected_model"]
        return dict(
            pid=222, model_sha256=selected["fine_sha256"], inference_enabled=True
        )

    def snapshot(self):
        result = copy.deepcopy(self.source)
        if self.started:
            result["pid"] = 222
            result["command"].append("--inference")
            result["service"]["exec_start"] = list(result["command"])
            result["model"]["selection"] = json.loads(self.files[self.path])[
                "selected_model"
            ]
            identity = result["config_files"][
                "/root/site/camera_environment_identity.json"
            ]
            identity["json"]["boot_registers"] = {"source": 1234}
            identity["json"]["verified_flash_release"]["manifest_sha256"] = sha(
                self.files[self.path]
            )
            identity["sha256"] = "new generated proof hash"
            controls = result["config_files"]["/root/site/image_controls.json"]
            controls["json"]["config"]["revision"] += 1
            controls["sha256"] = "new transaction revision hash"
        return result


class FixedDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.local = Path(self.temp.name) / "run"
        self.local.mkdir()
        self.bundle = Path(self.temp.name) / "bundle"
        self.config = dict(
            labels=["A", "B", "C"],
            input_size=[512, 288],
            input_transform="native_full_roi_fpn_integer_crop_v1",
            mean=[0, 0, 0],
            std=[1, 1, 1],
        )
        payloads = {
            "detector_int8.rknn": b"fake model bytes, never loaded",
            "config.json": json.dumps(self.config).encode(),
        }
        for name, raw in payloads.items():
            (self.local / name).write_bytes(raw)
        hashes = {name: sha(raw) for name, raw in payloads.items()}
        self.selection = dict(
            run="new-run",
            batch="fixture-batch",
            model_file="detector_int8.rknn",
            training_started_beijing="2026-10-31T10:00:00+08:00",
            training_completed_beijing="2026-10-31T10:01:00+08:00",
            hashes=hashes,
            fine_sha256=hashes["detector_int8.rknn"],
            config_sha256=hashes["config.json"],
        )
        self.camera = dict(
            busy=False,
            sensor=dict(
                shr0=2146,
                gain_code=43,
                sensor_transition=False,
                sensor_register_readback=True,
            ),
            isp=dict(
                config=dict(rg=376, bg=379, contrast=312, brightness=16, gaussian=32),
                wb_auto=False,
            ),
            automatic=dict(
                config=dict(frozen=True, contrast=False),
                enhancement=dict(
                    gain_q8=256,
                    strength_q8=64,
                    knots=[0, 8, 40, 96, 152, 184, 216, 248, 256],
                    revision=10,
                    effective_revision=10,
                ),
            ),
            processing_controls=dict(
                pending=False,
                fault=False,
                source_valid=True,
                effective=dict(words=[0, 319, 0, 0, 0, 0, 0, 0, 32, 64]),
            ),
        )
        self.manifest = dict(
            schema="rk_flash_release_v1",
            runtime_root="/root/site",
            python="/root/venv/bin/python",
            launch_mode="preview",
            selected_model=dict(run="old-run"),
            authorization=dict(user_answer="historical choice"),
            imaging={"old": "must not be replayed"},
            runtime_files={"/root/site/native_tracking.py": "immutable"},
            driver={"path": "unchanged"},
            environment={"VISION_MINIMAL60": "1"},
            source_identity={"firmware_sha256": "unchanged"},
        )
        command = [
            "/root/venv/bin/python",
            "-u",
            "/root/site/run_live.py",
            "--monitors",
            "HDMI-1",
        ]
        self.before = dict(
            pid=111,
            root="/root/site",
            cwd="/root/site",
            entry="/root/site/run_live.py",
            command=command,
            search_path=["/root/site"],
            modules={"entry": dict(path="/root/site/run_live.py", sha256="1" * 64)},
            model=dict(is_symlink=True, target="/root/site/old-model", selection=None),
            config_files={
                "/root/site/demo_config.json": dict(
                    sha256="2" * 64,
                    json=dict(rectangle=[704, 396, 512, 288], confidence=85),
                ),
                "/root/site/camera_environment_identity.json": dict(
                    sha256="3" * 64,
                    json=dict(
                        firmware_sha256="fixed",
                        boot_registers={"source": 1},
                        verified_flash_release=dict(
                            manifest_sha256="old", readback_sha256="sealed"
                        ),
                    ),
                ),
                "/root/site/image_controls.json": dict(
                    sha256="4" * 64, json=dict(config=dict(rg=376, revision=8))
                ),
            },
            service=dict(
                exec_start=list(command),
                exec_start_definition="unchanged fixed wrapper",
                working_directory="/root/site",
                environment={"VISION_MINIMAL60": "1"},
                unit_sha256="5" * 64,
            ),
        )
        self.board = FixedBoard(self.before, self.camera, self.manifest)
        self.job = SimpleNamespace(
            update=MagicMock(), check=MagicMock(), protected=False
        )
        self.probe = patch.object(
            deploy, "snapshot_runtime", side_effect=lambda board: board.snapshot()
        )
        self.probe.start()
        self.addCleanup(self.probe.stop)

    def run_deploy(self):
        return deploy.deploy_model(
            self.board, self.local, self.selection, self.bundle, self.job
        )

    def test_fixed_switch_preserves_entry_and_real_site_then_persists_only_selected_model(
        self,
    ):
        result = self.run_deploy()
        current = json.loads(self.board.files[self.board.path])
        self.assertEqual(current["launch_mode"], "auto_selected")
        self.assertEqual(current["selected_model"]["run"], "new-run")
        self.assertEqual(
            current["selected_model"]["fine_sha256"], self.selection["fine_sha256"]
        )
        self.assertEqual(
            current["imaging"], deploy.fixed_imaging_from_camera(self.camera)
        )
        for key in ("runtime_files", "driver", "environment", "source_identity"):
            self.assertEqual(current[key], self.manifest[key])
        self.assertTrue(result["persistent"])
        self.assertTrue(result["fixed_entry_preserved"])
        self.assertFalse(result["display_60fps_verified"])
        self.assertFalse(result["board_accuracy_verified"])
        self.assertEqual(self.board.atomic_paths[-1].split("/")[-1], "selection.json")
        self.assertFalse(
            any(path.startswith("/run/systemd/") for path in self.board.opened)
        )
        self.assertFalse(
            any("daemon-reload" in command for command in self.board.commands)
        )
        self.assertEqual(
            self.board.snapshot()["service"]["exec_start_definition"],
            self.before["service"]["exec_start_definition"],
        )
        self.assertFalse(self.job.protected)

    def test_nonfixed_is_legacy_and_unknown_fixed_command_is_rejected(self):
        self.board.definition = (
            "{ argv[]=/usr/bin/python3 /root/site/run_live.py ; pid=111 }"
        )
        self.assertIsNone(deploy.read_fixed_release(self.board))
        self.board.definition = "{ argv[]=/usr/bin/python3 /root/site/flash_boot/flash_boot.py --manifest /tmp/other.json ; pid=111 }"
        with self.assertRaisesRegex(ValueError, "wrapper/manifest"):
            deploy.read_fixed_release(self.board)
        self.assertFalse(self.board.started)

    def test_auto_state_is_preserved_and_changed_artifact_never_stops_service(self):
        self.camera["automatic"]["config"]["frozen"] = False
        imaging = deploy.fixed_imaging_from_camera(self.camera)
        self.assertIs(imaging["frozen"], False)
        self.assertEqual(imaging["saved_auto"], self.camera["automatic"]["config"])
        (self.local / "detector_int8.rknn").write_bytes(b"changed after choice")
        with self.assertRaisesRegex(ValueError, "选择后模型文件发生变化"):
            self.run_deploy()
        self.assertFalse(
            any("systemctl stop" in command for command in self.board.commands)
        )

    def test_regenerated_proof_is_allowed_but_real_parameter_and_source_changes_rejected(
        self,
    ):
        self.run_deploy()
        after = self.board.snapshot()
        self.assertEqual(
            deploy.fixed_runtime_differences(self.before, after, after["command"]), []
        )
        after["config_files"]["/root/site/image_controls.json"]["json"]["config"][
            "rg"
        ] += 1
        self.assertTrue(
            deploy.fixed_runtime_differences(self.before, after, after["command"])
        )
        after = self.board.snapshot()
        after["modules"]["entry"]["sha256"] = "altered"
        self.assertTrue(
            deploy.fixed_runtime_differences(self.before, after, after["command"])
        )

    def test_actual_counter_archive_script_preserves_final_old_counts_and_same_run(
        self,
    ):
        self.run_deploy()
        command = next(
            c for c in self.board.commands if "print(json.dumps(result))" in c
        )
        script = shlex.split(command)[-1]
        root = Path(self.temp.name) / "runtime"
        root.mkdir()
        (root / "demo_config.json").write_text(
            json.dumps(dict(rectangle=[704, 396, 512, 288]))
        )
        checkpoint = root / "counter_state.json"
        original = json.dumps(
            dict(run="old-run", rectangle=[704, 396, 512, 288], counts=[11, 22, 33])
        ).encode()
        checkpoint.write_bytes(original)
        translated = script.replace("/root/site", root.as_posix())
        target = (
            root
            / json.loads((self.bundle / "model_plan.json").read_text())[
                "transaction_directory"
            ].split("/root/site/")[1]
            / "counter_before_switch.json"
        )
        target.parent.mkdir(parents=True)
        with contextlib.redirect_stdout(io.StringIO()):
            exec(compile(translated, "<archive fixture>", "exec"), {})
        self.assertFalse(checkpoint.exists())
        self.assertEqual(target.read_bytes(), original)
        checkpoint.write_text(
            json.dumps(
                dict(run="new-run", rectangle=[704, 396, 512, 288], counts=[1, 2, 3])
            )
        )
        with contextlib.redirect_stdout(io.StringIO()):
            exec(compile(translated, "<same-run fixture>", "exec"), {})
        self.assertTrue(checkpoint.exists())
        self.assertEqual(target.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
