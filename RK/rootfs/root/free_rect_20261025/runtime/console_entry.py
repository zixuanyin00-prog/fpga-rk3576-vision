from pathlib import Path
import importlib.util
import sys

ROOT = Path("/root/free_rect_20261025/runtime")
OVERLAY = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(OVERLAY))
spec = importlib.util.spec_from_file_location(
    "rect_pipeline", OVERLAY / "rect_pipeline.py"
)
module = importlib.util.module_from_spec(spec)
sys.modules["rect_pipeline"] = module
spec.loader.exec_module(module)
import console_native

console_native.install_display_titles(ROOT)


import json as _ui_json
from pose_chart import ChartState as _PreviousChartState
import native_view as _ui_view

_chart_path = OVERLAY / "saved_chart_settings.json"
if not _chart_path.exists():
    _chart_path = ROOT / "saved_chart_settings.json"
if _chart_path.exists():
    _saved_ui = _ui_json.loads(_chart_path.read_text())

    class _SavedChartState(_PreviousChartState):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.mode = _saved_ui["mode"]
            self.interval = _saved_ui["interval"]
            self.pie_seconds = _saved_ui["pie_seconds"]
            self._display_settings_restored = False

        def update(self, *args, **kwargs):
            changed = super().update(*args, **kwargs)
            if not self._display_settings_restored and self.session is not None:
                self.enabled = set(_saved_ui["enabled"]) & {
                    f["key"] for f in self.fields
                }
                self.page = min(_saved_ui["page"], max(0, (len(self.fields) - 1) // 10))
                self._display_settings_restored = True
                self.serial += 1
            return changed

    _ui_view.ChartState = _SavedChartState

from run_live import main

if __name__ == "__main__":
    import cv2

    cv2.setNumThreads(1)
    sys.setswitchinterval(0.001)
    main()
