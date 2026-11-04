from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from console_runtime_probe import _remote_probe, probe_script


class RuntimeProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.overlay = self.root / "deployments" / "actual-running-version"
        self.overlay.mkdir(parents=True)
        self.entry = self.overlay / "console_entry.py"
        self.marker = self.root / "runtime_was_executed"
        self.entry.write_text(
            "from pathlib import Path\nimport sys\n"
            "ROOT=Path(" + repr(str(self.root)) + ")\n"
            "OVERLAY=Path(__file__).resolve().parent\n"
            "sys.path.insert(0,str(ROOT))\n"
            "sys.path.insert(0,str(OVERLAY))\n"
            "import native_view\n"
            "Path(" + repr(str(self.marker)) + ').write_text("incorrectly executed")\n',
            encoding="utf-8",
        )
        (self.root / "native_view.py").write_text("OLD_UI=True\n", encoding="utf-8")
        (self.overlay / "native_view.py").write_text(
            'import resistor_view\nraise RuntimeError("Probe must never import this")\n',
            encoding="utf-8",
        )
        (self.root / "resistor_view.py").write_text(
            "BASE_WIDGETS=True\n", encoding="utf-8"
        )
        (self.root / "demo_config.json").write_text(
            json.dumps(
                dict(
                    rectangle=[704, 396, 512, 288], confidence_percent=60, track_hits=1
                )
            ),
            encoding="utf-8",
        )
        (self.overlay / "saved_chart_settings.json").write_text(
            '{"mode":"pie"}', encoding="utf-8"
        )
        (self.root / "counter_state.json").write_text(
            '{"observed_total":123}', encoding="utf-8"
        )
        (self.root / "model").mkdir()
        (self.root / "model" / "detector_fp16.rknn").write_bytes(
            b"not an executable model"
        )
        (self.root / "model" / "config.json").write_text(
            '{"labels":["new"]}', encoding="utf-8"
        )
        self.options = dict(pid=None, entry=str(self.entry), root=str(self.root))

    def test_actual_overlay_wins_and_root_fallback_is_resolved_without_execution(self):
        result = _remote_probe(self.options)
        self.assertEqual(
            result["module_paths"]["native_view"], str(self.overlay / "native_view.py")
        )
        self.assertEqual(
            result["module_paths"]["resistor_view"], str(self.root / "resistor_view.py")
        )
        self.assertEqual(result["search_path"][:2], [str(self.overlay), str(self.root)])
        self.assertFalse(self.marker.exists())
        self.assertEqual(result["unsupported_path_mutations"], [])

    def test_volatile_counts_are_separate_and_live_settings_are_only_read(self):
        before = (self.root / "demo_config.json").read_bytes()
        result = _remote_probe(self.options)
        site = result["config_files"][str(self.root / "demo_config.json")]
        self.assertEqual(site["json"]["confidence_percent"], 60)
        self.assertEqual(site["json"]["rectangle"], [704, 396, 512, 288])
        self.assertNotIn(str(self.root / "counter_state.json"), result["config_files"])
        self.assertEqual(
            result["state_files"][str(self.root / "counter_state.json")]["json"][
                "observed_total"
            ],
            123,
        )
        self.assertEqual((self.root / "demo_config.json").read_bytes(), before)
        self.assertEqual(
            result["model"]["weights_path"],
            str(self.root / "model" / "detector_fp16.rknn"),
        )

    def test_unknown_search_path_change_is_reported_and_remote_script_compiles(self):
        with self.entry.open("a", encoding="utf-8") as stream:
            stream.write("sys.path.append(calculate_unknown_path())\n")
        result = _remote_probe(self.options)
        self.assertTrue(
            any("append" in row for row in result["unsupported_path_mutations"])
        )
        compile(probe_script(**self.options), "remote_probe", "exec")
        self.assertFalse(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
