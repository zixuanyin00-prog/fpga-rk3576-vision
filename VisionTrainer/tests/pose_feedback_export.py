import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import store as db
from service import Service

OUT = db.ROOT / "tests/artifacts/pose_feedback_20261025"
db.DATA = OUT / "projects"
id = db.read(OUT / "fixture.json")["project"]
s = Service()
s.settings["steps"] = 50
s.train(
    id,
    steps=50,
    batch="b",
    initialization="random",
    use_feedback=True,
    scene_confirmation=True,
)
while s.busy():
    time.sleep(0.5)
print(s.job, flush=True)
if s.job["state"] != "complete":
    raise RuntimeError(s.job["message"])
p = db.load(id)
out = db.project_path(id) / "runs" / p["runs"][-1]
config = db.read(out / "config.json")
assert len(config["labels"]) == 5 and len(config["class_ids"]) == 5
import onnx
import onnxruntime as ort
import numpy as np
import torch
from core.model import ResistorNet
from core.data import tensor

onnx.checker.check_model(onnx.load(str(out / "detector.onnx")))
session = ort.InferenceSession(
    str(out / "detector.onnx"), providers=["CPUExecutionProvider"]
)
x = tensor(
    np.frombuffer(
        (db.project_path(id) / p["frames"][0]["file"]).read_bytes(), np.uint8
    ).reshape(288, 512, 3)
)[None]


net = ResistorNet(states=5).eval()
net.load_state_dict(
    torch.load(out / "detector.pt", weights_only=True, map_location="cpu")
)
with torch.no_grad():
    expected = [v.numpy() for v in net(x)]
actual = session.run(None, {session.get_inputs()[0].name: x.numpy()})
error = max(float(np.max(np.abs(a - b))) for a, b in zip(expected, actual))
assert error < 1e-4, error
assert actual[1].shape[1] == 5
conversion = db.read(out / "conversion.json")
db.write(
    OUT / "export_result.json",
    dict(
        passed=True,
        run=p["runs"][-1],
        folder=str(out),
        fixture="synthetic shapes; not accuracy acceptance",
        original_images=db.read(out / "training_manifest.json")["original_images"],
        class_count=5,
        gpu=db.read(out / "device.json"),
        onnx_checked=True,
        onnx_runtime_class_channels=actual[1].shape[1],
        max_absolute_error=error,
        rknn=conversion,
        board_connected=False,
    ),
)
print("PASS actual GPU training and ONNX parity; RKNN:", conversion, flush=True)
