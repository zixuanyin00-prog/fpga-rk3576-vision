from contextlib import nullcontext
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import console_store as db
from console_action import CONFIG_PATH, STATUS_PATH
from console_board import Board
from console_workflow import Workflow


class Stream:
    def __init__(self, board, path):
        self.board = board
        self.path = path
        self.parts = []

    def __enter__(self):
        return self

    def write(self, data):
        self.parts.append(data.encode("utf-8") if isinstance(data, str) else data)

    def __exit__(self, *args):
        self.board.files[self.path] = b"".join(self.parts)


class FakeBoard:
    def __init__(self):
        self.remote = "/root/site"
        self.boot = "fixture-boot"
        self.pid = 0
        self.running = False
        self.path = self.remote + "/flash_boot/release.json"
        self.commands = []
        self.manifest = dict(
            schema="rk_flash_release_v1",
            runtime_root=self.remote,
            launch_mode="auto_selected",
            selected_model=dict(run="old-run", fine_sha256="a" * 64),
            imaging=dict(unchanged=True),
        )
        self.files = {
            self.path: json.dumps(self.manifest).encode(),
            CONFIG_PATH: json.dumps(
                dict(
                    schema_version=1,
                    enabled=True,
                    active_high=False,
                    target_labels=["old-label"],
                    model_sha256="a" * 64,
                    delay_ms=120,
                    pulse_ms=8,
                    trigger_mode="center_line",
                )
            ).encode(),
        }
        self.s = SimpleNamespace(
            open=lambda path, mode: Stream(self, path), posix_rename=self.rename
        )

    def rename(self, source, target):
        self.files[target] = self.files.pop(source)

    def exists(self, path):
        return path in self.files or path == STATUS_PATH

    def bytes(self, path):
        if path == "/proc/uptime":
            return b"100.5 0"
        if path == STATUS_PATH:
            return json.dumps(
                dict(
                    config=json.loads(self.files[CONFIG_PATH]),
                    stopped=False,
                    active=False,
                    pending=0,
                    awaiting_confirmation=0,
                    recorded_monotonic=100.0,
                    error=None,
                    config_error=None,
                )
            ).encode()
        return self.files[path]

    def command(self, text, timeout=12):
        self.commands.append(text)
        if text.startswith("systemctl cat "):
            return b"fixture fixed unit"
        if text.startswith("systemctl reset-failed "):
            self.running = True
        if text.startswith("systemctl show "):
            return b"222\n" if self.running else b"0\n"
        return b""

    def fresh(self, name):
        return dict(pid=222, inference_enabled=False, selected_model=None)

    def status(self):
        return dict(
            runtime=self.fresh("runtime_status.json"),
            action_output=dict(
                installed=True, fresh=True, config=json.loads(self.files[CONFIG_PATH])
            ),
        )


class PreviewBoundaryTests(unittest.TestCase):
    def test_stopped_board_connection_is_explicit_and_uses_validated_runtime_root(self):
        channel = SimpleNamespace(recv_exit_status=lambda: 0)

        def connect():
            sftp = SimpleNamespace(
                get_channel=lambda: SimpleNamespace(settimeout=MagicMock()),
                readlink=MagicMock(),
                open=lambda *args: io.BytesIO(b"fixture-boot\n"),
                close=MagicMock(),
            )

            def command(*args, **kwargs):
                out = io.BytesIO(b"0\n")
                out.channel = channel
                return None, out, io.BytesIO()

            client = SimpleNamespace(
                open_sftp=lambda: sftp, exec_command=command, close=MagicMock()
            )
            return client, sftp

        client, sftp = connect()
        service = SimpleNamespace(
            Service=lambda: SimpleNamespace(connect=lambda: client)
        )
        with patch.dict(sys.modules, service=service):
            with self.assertRaisesRegex(ValueError, "未运行"):
                Board({"remote": "/root/site"}).__enter__()
        sftp.close.assert_called_once()
        client.close.assert_called_once()
        sftp.readlink.assert_not_called()
        client, sftp = connect()
        service = SimpleNamespace(
            Service=lambda: SimpleNamespace(connect=lambda: client)
        )
        with patch.dict(sys.modules, service=service):
            with Board({"remote": "/root/site"}, allow_stopped=True) as board:
                self.assertEqual(
                    (board.pid, board.remote, board.boot),
                    (0, "/root/site", "fixture-boot"),
                )
        sftp.close.assert_called_once()
        client.close.assert_called_once()
        sftp.readlink.assert_not_called()

    def test_failed_fixed_service_enters_persistent_preview_without_model_operations(
        self,
    ):
        from console_preview import enter_preview

        board = FakeBoard()
        job = SimpleNamespace(
            id="fixture", check=MagicMock(), update=MagicMock(), protected=False
        )
        fixed = dict(
            path=board.path,
            raw=board.files[board.path],
            manifest=copy.deepcopy(board.manifest),
            command=["python", "flash_boot.py"],
        )
        with tempfile.TemporaryDirectory() as folder, patch(
            "console_preview.read_fixed_release", return_value=fixed
        ):
            result = enter_preview(board, "new-flow", Path(folder), job)
        self.assertEqual((result["mode"], result["pid"]), ("preview", 222))
        self.assertFalse(result["model_loaded"])
        self.assertFalse(job.protected)
        expected = dict(board.manifest, launch_mode="preview")
        self.assertEqual(json.loads(board.files[board.path]), expected)
        self.assertFalse(json.loads(board.files[CONFIG_PATH])["enabled"])
        self.assertTrue(
            any(
                command.startswith("systemctl reset-failed ")
                for command in board.commands
            )
        )
        self.assertFalse(
            any(
                "--inference" in command or "model-link" in command
                for command in board.commands
            )
        )
        self.assertFalse(any("/run/systemd/" in path for path in board.files))

    def test_existing_healthy_persistent_preview_does_not_restart(self):
        from console_preview import enter_preview

        board = FakeBoard()
        board.pid = 222
        board.running = True
        board.manifest["launch_mode"] = "preview"
        board.files[board.path] = json.dumps(board.manifest).encode()
        job = SimpleNamespace(
            id="fixture", check=MagicMock(), update=MagicMock(), protected=False
        )
        fixed = dict(
            path=board.path,
            raw=board.files[board.path],
            manifest=board.manifest,
            command=[],
        )
        with tempfile.TemporaryDirectory() as folder, patch(
            "console_preview.read_fixed_release", return_value=fixed
        ):
            enter_preview(board, "new-flow", Path(folder), job)
        self.assertFalse(
            any(
                "systemctl stop fpga-" in command or "systemctl reset-failed" in command
                for command in board.commands
            )
        )

    def test_live_preview_restart_preserves_actual_imaging_and_auto_state_instead_of_old_manifest(
        self,
    ):
        from console_preview import enter_preview
        from console_model_deploy import fixed_imaging_from_camera

        camera = dict(
            sensor=dict(
                shr0=901,
                gain_code=7,
                sensor_transition=False,
                sensor_register_readback=True,
            ),
            isp=dict(
                wb_auto=False,
                config=dict(rg=257, bg=259, contrast=256, brightness=0, gaussian=32),
            ),
            automatic=dict(
                config=dict(frozen=True),
                enhancement=dict(
                    gain_q8=256,
                    strength_q8=64,
                    knots=[0, 255],
                    revision=9,
                    effective_revision=9,
                ),
            ),
            processing_controls=dict(
                pending=False,
                fault=False,
                source_valid=True,
                effective=dict(words=list(range(10))),
            ),
        )
        for frozen in (True, False):
            with self.subTest(frozen=frozen):
                board = FakeBoard()
                board.pid = 111
                actual = copy.deepcopy(camera)
                actual["automatic"]["config"]["frozen"] = frozen
                runtime_fresh = board.fresh
                board.fresh = lambda name: (
                    actual
                    if name == "camera_console/status.json"
                    else runtime_fresh(name)
                )
                before = board.files[board.path]
                fixed = dict(
                    path=board.path,
                    raw=before,
                    manifest=copy.deepcopy(board.manifest),
                    command=[],
                )
                job = SimpleNamespace(
                    id="fixture", check=MagicMock(), update=MagicMock(), protected=False
                )
                with tempfile.TemporaryDirectory() as folder, patch(
                    "console_preview.read_fixed_release", return_value=fixed
                ):
                    enter_preview(board, "new-flow", Path(folder), job)
                    saved = json.loads(board.files[board.path])
                    self.assertEqual(
                        saved,
                        dict(
                            board.manifest,
                            launch_mode="preview",
                            imaging=fixed_imaging_from_camera(actual),
                        ),
                    )
                    self.assertIs(saved["imaging"]["frozen"], frozen)
                    self.assertEqual(
                        saved["imaging"]["saved_auto"], actual["automatic"]["config"]
                    )
                self.assertFalse(json.loads(board.files[CONFIG_PATH])["enabled"])
                self.assertFalse(job.protected)

    def test_unknown_entry_preserves_files_and_refuses_unsafe_fallback(self):
        from console_preview import enter_preview

        board = FakeBoard()
        before = copy.deepcopy(board.files)
        job = SimpleNamespace(
            id="fixture", check=MagicMock(), update=MagicMock(), protected=False
        )
        with tempfile.TemporaryDirectory() as folder, patch(
            "console_preview.read_fixed_release", return_value=None
        ):
            with self.assertRaisesRegex(ValueError, "固定启动入口"):
                enter_preview(board, "new-flow", Path(folder), job)
        self.assertEqual(board.files, before)
        self.assertFalse(board.commands)

    def test_preview_roi_is_native_bounded_while_unknown_or_model_mode_stays_strict(
        self,
    ):
        board = Board({})
        board.transaction = MagicMock(return_value=({"ok": True}, "/unused"))
        board.fresh = MagicMock(
            return_value=dict(inference_enabled=False, inference={})
        )
        values = dict(rectangle=[0, 792, 512, 288], confidence_percent=85, track_hits=3)
        board.configure(values, SimpleNamespace())
        values = dict(values, rectangle=[0, 0, 720, 720])
        board.configure(values, SimpleNamespace())
        calls = board.transaction.call_count
        for rectangle in ([0, 0, 721, 720], [1500, 0, 512, 288]):
            with self.assertRaises(ValueError):
                board.configure(dict(values, rectangle=rectangle), SimpleNamespace())
        self.assertEqual(board.transaction.call_count, calls)
        board.fresh.return_value = {"inference": {}}
        with self.assertRaisesRegex(ValueError, "模型输入尺寸"):
            board.configure(values, SimpleNamespace())


class FlowIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data = patch.object(db, "DATA", Path(self.temporary.name))
        self.data.start()
        self.addCleanup(self.data.stop)
        self.workflow = Workflow.__new__(Workflow)
        self.workflow.settings = {}
        self.workflow.selections = {"old": {"flow": "old"}}
        self.submitted = []

        def submit(flow, page, kind, action):
            self.submitted.append((flow, kind, action))
            return dict(id="job", flow=flow, kind=kind, state="queued")

        self.workflow.jobs = SimpleNamespace(
            submit=submit,
            stop=MagicMock(),
            state=lambda identity: [],
            resource=lambda *args: nullcontext(),
        )

    def test_new_returns_pending_flow_and_queues_preview_without_connecting(self):
        old = db.create("old")
        (db.folder(old["id"]) / "retained-model.pt").write_bytes(
            b"fixture never loaded"
        )
        with patch("console_workflow.Board") as board:
            new = self.workflow.dispatch(
                "new", dict(flow=old["id"], name="new"), "page"
            )
        board.assert_not_called()
        self.assertEqual(
            new["runtime_isolation"], dict(state="pending", mode="preview")
        )
        self.assertEqual(self.submitted[0][:2], (new["id"], "preview"))
        self.assertFalse(self.workflow.selections)
        self.assertEqual(
            (db.folder(old["id"]) / "retained-model.pt").read_bytes(),
            b"fixture never loaded",
        )
        for action in ("freeze", "capture", "prepare_deploy", "action_output"):
            with self.assertRaisesRegex(ValueError, "纯预览"):
                self.workflow.dispatch(action, dict(flow=new["id"]), "page")

    def test_failed_preview_is_recorded_and_can_be_retried(self):
        new = self.workflow.dispatch("new", dict(name="new"), "page")
        board = MagicMock()
        board.__enter__.return_value = board
        job = SimpleNamespace(id="job", protected=False)
        with patch("console_workflow.Board", return_value=board), patch(
            "console_preview.enter_preview", side_effect=RuntimeError("fixture failed")
        ):
            with self.assertRaisesRegex(RuntimeError, "fixture failed"):
                self.workflow.enter_preview(new["id"], job)
        flow = db.load(new["id"])
        self.assertEqual(flow["runtime_isolation"]["state"], "error")
        result = self.workflow.dispatch("preview", dict(flow=new["id"]), "page")
        self.assertEqual(result["kind"], "preview")
        self.assertEqual(db.load(new["id"])["runtime_isolation"]["state"], "pending")

    def test_gpio_cannot_enable_other_flow_model_or_enable_before_explicit_deploy(self):
        flow = db.create("new")
        flow["runtime_isolation"] = dict(state="ready", mode="preview")
        db.save(flow)
        values = dict(enabled=True, model_sha256="a" * 64)
        with self.assertRaisesRegex(ValueError, "本流程模型"):
            self.workflow.dispatch(
                "action_output", dict(flow=flow["id"], values=values), "page"
            )
        flow["runtime_isolation"] = dict(
            state="ready", mode="inference", model_sha256="b" * 64
        )
        db.save(flow)
        with self.assertRaisesRegex(ValueError, "本流程模型"):
            self.workflow.dispatch(
                "action_output", dict(flow=flow["id"], values=values), "page"
            )


if __name__ == "__main__":
    unittest.main()
