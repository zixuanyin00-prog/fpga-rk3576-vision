import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from console_runtime_options import SELECTION_FIELDS, apply, state
from test_console_preview_flow import FakeBoard


class RuntimeOptionsTests(unittest.TestCase):
    def setUp(self):
        self.board = FakeBoard()
        self.board.pid = 222
        self.board.running = True
        self.board.manifest.update(
            launch_mode="preview",
            selected_model=dict(
                run="fixture-run",
                batch="fixture-batch",
                training_started_beijing="2026-10-31 12:00:00",
                training_completed_beijing="2026-10-31 12:01:00",
                fine_sha256="a" * 64,
                config_sha256="b" * 64,
                project_name="fixture-scene",
            ),
        )
        self.board.files[self.board.path] = json.dumps(self.board.manifest).encode()
        self.job = SimpleNamespace(
            id="fixture", check=MagicMock(), update=MagicMock(), protected=False
        )
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.fixed = dict(
            path=self.board.path,
            raw=self.board.files[self.board.path],
            manifest=copy.deepcopy(self.board.manifest),
        )

    def test_status_uses_live_values_and_retains_exact_model_training_identity(self):
        actual = state(
            self.board,
            dict(inference_enabled=False, runtime_options=dict(show_roi=False)),
        )
        self.assertFalse(actual["inference_enabled"])
        self.assertFalse(actual["show_roi"])
        self.assertTrue(actual["requested_show_roi"])
        self.assertEqual(
            actual["available_model"], self.board.manifest["selected_model"]
        )
        self.assertEqual(
            actual["available_model_confirm_fields"], list(SELECTION_FIELDS)
        )
        self.assertFalse(self.board.commands)

    def test_roi_display_only_persists_and_reads_back_without_restarting_or_model_operations(
        self,
    ):
        def fresh(name):
            path = self.board.remote + "/runtime_options.json"
            option = (
                json.loads(self.board.files[path])["show_roi"]
                if path in self.board.files
                else True
            )
            return dict(
                pid=222,
                inference_enabled=False,
                selected_model=None,
                runtime_options=dict(show_roi=option),
            )

        self.board.fresh = fresh
        self.board.status = lambda: dict(
            runtime_options=state(self.board, fresh("runtime_status.json"))
        )
        with patch(
            "console_runtime_options.read_fixed_release", return_value=self.fixed
        ):
            result = apply(
                self.board,
                dict(inference_enabled=False, show_roi=False),
                False,
                None,
                self.folder.name,
                self.job,
            )
        self.assertFalse(result["runtime_options"]["show_roi"])
        self.assertFalse(self.job.protected)
        self.assertFalse(self.board.commands)
        self.assertEqual(
            json.loads(self.board.files[self.board.remote + "/runtime_options.json"]),
            dict(schema_version=1, show_roi=False),
        )
        self.assertEqual(self.board.files[self.board.path], self.fixed["raw"])

    def test_enable_requires_this_explicit_exact_identity_before_any_write(self):
        before = copy.deepcopy(self.board.files)
        with patch(
            "console_runtime_options.read_fixed_release", return_value=self.fixed
        ):
            for confirmed, selected in (
                (False, None),
                (
                    True,
                    dict(self.board.manifest["selected_model"], fine_sha256="c" * 64),
                ),
            ):
                with self.subTest(confirmed=confirmed):
                    with self.assertRaisesRegex(ValueError, "明确确认"):
                        apply(
                            self.board,
                            dict(inference_enabled=True, show_roi=True),
                            confirmed,
                            selected,
                            self.folder.name,
                            self.job,
                        )
        self.assertEqual(self.board.files, before)
        self.assertFalse(self.board.commands)
        with self.assertRaisesRegex(ValueError, "两个开关"):
            apply(
                self.board,
                dict(inference_enabled=1, show_roi=True),
                True,
                None,
                self.folder.name,
                self.job,
            )


if __name__ == "__main__":
    unittest.main()
