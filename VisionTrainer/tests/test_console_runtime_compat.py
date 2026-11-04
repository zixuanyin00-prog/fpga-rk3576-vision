from pathlib import Path
from types import SimpleNamespace
import ast
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from console_runtime import patch_pipeline, patch_preview, patch_view

SNAPSHOT = ROOT / "evidence" / "deploy_compat_20261027"


class RuntimeCompatibility(unittest.TestCase):
    def test_actual_raw_crlf_and_lf_sources_compile_and_are_idempotent(self):
        for name, patch in [
            ("native_view.py", patch_view),
            ("shared_preview.py", patch_preview),
            ("rect_pipeline.py", patch_pipeline),
        ]:
            raw = (SNAPSHOT / name).read_bytes().decode("utf-8")
            for source in (raw, raw.replace("\r\n", "\n")):
                with self.subTest(name=name, crlf="\r\n" in source):
                    patched = patch(source)
                    compile(patched, name, "exec")
                    self.assertEqual(patch(patched), patched)

    def test_existing_active_ui_and_motion_preview_are_preserved(self):
        for name, patch in [
            ("native_view.py", patch_view),
            ("shared_preview.py", patch_preview),
        ]:
            source = (SNAPSHOT / "active_entry" / name).read_bytes().decode("utf-8")
            self.assertEqual(patch(source), source)

    def test_formatted_display_keeps_hover_cache_field(self):
        source = (SNAPSHOT / "native_view.py").read_bytes().decode("utf-8")
        source = source.replace(
            "self.chart.serial)", 'self.chart.serial,getattr(self,"hover_serial",0))'
        )
        source = ast.unparse(ast.parse(source))
        patched = patch_view(source)
        self.assertIn("hover_serial", patched)
        self.assertEqual(patch_view(patched), patched)
        compile(patched, "native_view.py", "exec")

    def test_classification_refresh_is_cached_for_half_a_second(self):
        source = patch_view((SNAPSHOT / "native_view.py").read_bytes().decode("utf-8"))
        cls = next(
            n
            for n in ast.parse(source).body
            if isinstance(n, ast.ClassDef) and n.name == "ScreenView"
        )
        show = next(
            n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "show"
        )
        first = next(
            i
            for i, n in enumerate(show.body)
            if isinstance(n, ast.Assign)
            and isinstance(n.targets[0], ast.Name)
            and n.targets[0].id == "sampled"
        )
        last = next(
            i
            for i, n in enumerate(show.body)
            if isinstance(n, ast.Assign)
            and isinstance(n.targets[0], ast.Name)
            and n.targets[0].id == "key"
        )

        harness = ast.parse("def refresh(self,row,began):\n    pass")
        harness.body[0].body = show.body[first : last + 1] + [
            ast.Return(value=ast.Name(id="key", ctx=ast.Load()))
        ]
        ast.fix_missing_locations(harness)
        namespace = {}
        exec(compile(harness, "cached_ui_boundary", "exec"), namespace)
        chart = SimpleNamespace(update=lambda row, now: False, serial=3)
        view = SimpleNamespace(
            chart=chart,
            ui_metrics={},
            measured={"main": 60.0},
            w=1920,
            h=1080,
            full=True,
        )
        refresh = namespace["refresh"]
        first_key = refresh(
            view,
            {"counting": {"pose": "first"}, "fine_rate": 30, "model_id": "selected"},
            1.0,
        )
        self.assertEqual(view.ui_metrics["counting"], {"pose": "first"})
        self.assertEqual(refresh(view, {"counting": {"pose": "later"}}, 1.1), first_key)
        self.assertEqual(view.ui_metrics["counting"], {"pose": "first"})
        self.assertNotEqual(
            refresh(view, {"counting": {"pose": "later"}}, 1.5), first_key
        )
        self.assertEqual(view.ui_metrics["counting"], {"pose": "later"})

    def test_unknown_ui_condition_is_explained_before_deployment(self):
        source = (SNAPSHOT / "native_view.py").read_bytes().decode("utf-8")
        source = source.replace(
            "if sampled or not self.ui_metrics:", 'if row.get("unknown"):'
        )
        with self.assertRaisesRegex(ValueError, "native_view.py:.*UI"):
            patch_view(source)

    def test_pipeline_missing_methods_have_actionable_errors(self):
        with self.assertRaisesRegex(ValueError, "rect_pipeline.py:.*RectPipeline"):
            patch_pipeline("class Unrelated: pass")
        with self.assertRaisesRegex(ValueError, "rect_pipeline.py:.*_infer"):
            patch_pipeline("class RectPipeline: pass\nclass Tracks: pass")

    def test_distinct_full_roi_pipeline_is_rejected_without_rewriting_it(self):
        source = (
            (SNAPSHOT / "active_entry" / "rect_pipeline.py")
            .read_bytes()
            .decode("utf-8")
        )
        with self.assertRaisesRegex(ValueError, "rect_pipeline.py:"):
            patch_pipeline(source)

    def test_preview_rejects_unknown_classification_origin(self):
        source = (
            (SNAPSHOT / "active_entry" / "shared_preview.py")
            .read_bytes()
            .decode("utf-8")
        )
        source = source.replace(
            "classification_observations=row.get('results',[])",
            "classification_observations=unrelated",
        )
        with self.assertRaisesRegex(ValueError, "shared_preview.py:"):
            patch_preview(source)


if __name__ == "__main__":
    unittest.main()
