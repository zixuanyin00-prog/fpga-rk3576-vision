from pathlib import Path
import json
import re
import time
from focus_metadata import validate_region


class TrainingCaptureBridge:
    def __init__(self, root):
        self.root = Path(root)
        self.folder = self.root / "training_control"
        self.folder.mkdir(exist_ok=True)
        self.next_poll = 0

    def poll(self, camera, apply_region, busy=False):
        if time.monotonic() < self.next_poll:
            return False
        self.next_poll = time.monotonic() + 0.25
        path = self.folder / "request.json"
        if not path.exists():
            return False
        try:
            request = json.loads(path.read_text())
            path.unlink()
            id = request.get("id", "")
        except (ValueError, AttributeError):
            path.unlink(missing_ok=True)
            return False
        if not re.fullmatch(r"[A-Za-z0-9_]{1,80}", id):
            return False
        out = self.folder / id
        if out.exists():
            return False
        out.mkdir()
        changed = False
        try:
            if busy:
                raise RuntimeError("A capture batch is active; wait until it finishes")
            origin = request.get("origin", [800, 360])
            x, y = origin
            spec = dict(
                origin=origin,
                size=[512, 288],
                native_size=[1920, 1080],
                body_center_y=[y + 8, y + 280],
                complete_body_margin=8,
                description="User calibrated training ROI; raw native pixels",
            )
            validate_region(spec)
            if request["kind"] not in ("probe", "region"):
                raise ValueError("Unsupported training command")
            preview = camera.latest
            if preview is None:
                raise RuntimeError("No overview available")
            (out / "overview.rgb").write_bytes(preview.rgb.tobytes())
            frames = []
            for i in range(3):
                _, frame = camera.acquire(True, x, y)
                if (
                    not frame.roi
                    or (frame.x, frame.y) != (x, y)
                    or frame.rgb.shape != (288, 512, 3)
                ):
                    raise RuntimeError("Native ROI metadata mismatch")
                (out / f"native_{i}.rgb").write_bytes(frame.rgb.tobytes())
                frames.append(
                    dict(
                        file=f"native_{i}.rgb",
                        source_frame_id=frame.source_frame_id,
                        origin=[frame.x, frame.y],
                        native_roi=True,
                    )
                )
            if request["kind"] == "region":
                temp = self.root / "focus_region.tmp"
                temp.write_text(json.dumps(spec, indent=2))
                temp.replace(self.root / "focus_region.json")
                apply_region(spec)
                changed = True
            result = dict(
                ok=True,
                id=id,
                region=spec,
                frames=frames,
                overview=dict(
                    file="overview.rgb",
                    source_frame_id=preview.source_frame_id,
                    native_roi=False,
                    source_size=[1920, 1080],
                    size=[512, 288],
                ),
                pixels_transformed_on_cpu=False,
            )
        except Exception as e:
            result = dict(ok=False, id=id, error=str(e))
        temp = out / "result.tmp"
        temp.write_text(json.dumps(result, indent=2))
        temp.replace(out / "result.json")
        return changed
