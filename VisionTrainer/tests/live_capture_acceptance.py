from pathlib import Path
import sys
import json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from service import Service, DEFAULTS
from core import store as db

actual = db.load("20261023_220107_88f08d78")
db.DATA = ROOT / "tests/artifacts/capture_fix_live"
p = db.create("Isolated capture and display acceptance")
p["calibration"] = actual["calibration"]
db.save(p)
s = Service()
s.settings = {**s.settings, "frames": 80, "hz": 2, "stride": 4}
s.job = {}
s.capture_action(p["id"], "validation")
p = db.load(p["id"])
capture = p["last_capture"]
print(json.dumps(capture, ensure_ascii=True, indent=2), flush=True)
assert capture["source_frames"] == 80 and len(p["frames"]) == 20
assert capture["display"]["verified"], capture["display"]
db.write(
    ROOT / "evidence/capture_fix_acceptance.json",
    dict(
        project=p["id"],
        folder=str(db.project_path(p["id"])),
        capture=capture,
        recognition_accuracy_tested=False,
    ),
)
