import copy
import io
import json
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from board.dynamic_audit import DynamicAudit, PROTOCOL
from core.environment15 import approved_profile


class CaptureSimulator:
    def __init__(self, root, remote, point=(800, 470), faults=()):
        self.remote = remote.rstrip("/")
        self.point = list(point)
        self.faults = set(faults)
        self.files = {}
        self.requests = []
        self.probes = []
        self.downloads = []
        self.closed = False
        self.boot_id = "simulated_boot"
        self.now = 100.0
        self.next_source = 1000
        self.identity = dict(
            boot_id=self.boot_id,
            runtime_session_id="simulated_runtime",
            region_version="simulated_region",
            isp_generation=3,
            origin=self.point,
        )
        self.sensor = dict(
            shr0=100,
            gain_code=0,
            vmax=2250,
            hmax=550,
            sensor_generation=4,
            sensor_stable_source_id=100,
            sensor_transition=False,
            sensor_register_readback=True,
        )
        self.camera = dict(
            recorded_monotonic=self.now,
            identity=self.identity,
            sensor=self.sensor,
            isp=dict(
                config=dict(rg=256, bg=256, contrast=256, brightness=0, gaussian=0),
                wb_auto=False,
            ),
            automatic=dict(
                config=dict(frozen=True),
                enhancement=dict(
                    revision=5,
                    effective_revision=5,
                    gain_q8=256,
                    strength_q8=0,
                    knots=[0, 255],
                ),
            ),
            busy=False,
        )
        self.runtime = dict(
            pid=12,
            recorded_monotonic=self.now,
            environment_identity=self.identity,
            display_outputs=["HDMI-1", "DSI-1"],
            preview=dict(preview_frames=100, source_gaps=0),
            inference=dict(
                native_roi=dict(origin=self.point, size=[512, 288]),
                sensor_context=dict(sensor_generation=4),
                roi_calls_per_second=30,
                coarse_calls_per_second=60,
                fine_calls_per_second=30,
            ),
        )
        self.profile = approved_profile(self.camera, self.runtime, self.now, self.point)
        self.audit = DynamicAudit(root)
        self.control = self.remote + "/training_control"

    def json_bytes(self, data):
        return json.dumps(data).encode()

    def local(self, path):
        prefix = self.remote + "/dynamic_audit/"
        if not path.startswith(prefix):
            return None
        parts = Path(path[len(prefix) :]).parts
        if any(p in (".", "..") for p in parts):
            raise ValueError("invalid simulated path")
        return self.audit.root.joinpath(*parts)

    def open(self, path, mode="r"):
        if mode == "w":
            owner = self

            class Writer(io.StringIO):
                def close(self):
                    if not self.closed:
                        owner.files[path] = self.getvalue().encode()
                    super().close()

            return Writer()
        if path == "/proc/sys/kernel/random/boot_id":
            return io.BytesIO(self.boot_id.encode())
        if path == "/proc/uptime":
            return io.BytesIO(str(self.now).encode() + b" 0")
        if path == self.remote + "/runtime_status.json":
            data = self.runtime
        elif path == self.remote + "/camera_console/status.json":
            data = self.camera
        elif path == self.remote + "/camera_environment/sensor_status.json":
            data = self.sensor
        elif path in (
            self.remote + "/display_HDMI-1.json",
            self.remote + "/display_DSI-1.json",
        ):
            data = dict(
                recorded_monotonic=self.now,
                unique_frames=100,
                skipped_source_frames=0,
                presentation=dict(submitted=100, completed=100),
            )
        elif path in self.files:
            return io.BytesIO(self.files[path])
        else:
            local = self.local(path)
            if local is None or not local.is_file():
                raise FileNotFoundError(path)
            raw = local.read_bytes()
            if local.name == "status.json" and "bad_response_protocol" in self.faults:
                data = json.loads(raw)
                data["protocol"] = "legacy-written"
                return io.BytesIO(self.json_bytes(data))
            return io.BytesIO(raw)
        return io.BytesIO(self.json_bytes(data))

    def stat(self, path):
        local = self.local(path)
        if (
            path == self.control
            or path in self.files
            or local is not None
            and local.exists()
        ):
            return SimpleNamespace(st_size=0)
        raise FileNotFoundError(path)

    def rename(self, source, target):
        if target in self.files:
            raise FileExistsError(target)
        raw = self.files.pop(source)
        request = json.loads(raw)
        if target == self.control + "/request.json":
            self.probes.append(request)
            self.make_probe(request)
            return
        if target != self.remote + "/dynamic_audit/request.json":
            raise AssertionError("unexpected request destination")
        self.requests.append(copy.deepcopy(request))

        local = self.local(target)
        if local.exists():
            raise FileExistsError(target)
        local.write_bytes(raw)
        for attempt in range(request["max_attempts"]):
            source_id = self.next_source
            self.next_source += 1
            if "duplicate_source" in self.faults:
                source_id = 1000
            if "before_stable_fence" in self.faults:
                source_id = self.sensor["sensor_stable_source_id"]
            pixel_id = 1 if "duplicate_pixels" in self.faults else source_id
            rgb = np.empty((288, 512, 3), np.uint8)
            rgb[:] = [pixel_id % 256, (pixel_id // 256) % 256, 7]
            point = [800, 360] if "wrong_frame_region" in self.faults else self.point
            frame = SimpleNamespace(
                rgb=rgb, source_frame_id=source_id, roi=True, x=point[0], y=point[1]
            )
            if "reject_first_two" in self.faults:
                frame.quality_ok = attempt >= 2
            context = dict(
                self.identity,
                sensor_generation=self.sensor["sensor_generation"],
                sensor_transition=False,
            )

            self.audit.next_poll = self.audit.next_sample = 0
            self.audit.offer(frame, [], None, [], self.now, None, context=context)
            self.audit.queue.join()
            if self.audit.request is None:
                break
            if "cancel_after_first" in self.faults and attempt == 0:
                (self.audit.root / ("cancel_" + request["id"] + ".json")).write_text(
                    "{}"
                )
        self.settle_status(request["id"])

        self.now += 1
        self.runtime["recorded_monotonic"] = self.camera["recorded_monotonic"] = (
            self.now
        )
        if "restart_after_capture" in self.faults:
            self.runtime["pid"] += 1
        if "sensor_change_after_capture" in self.faults:
            self.sensor["sensor_generation"] += 1

    def settle_status(self, id):
        deadline = time.monotonic() + 3
        path = self.audit.root / id / "status.json"
        while time.monotonic() < deadline:
            try:
                status = json.loads(path.read_bytes())
                if status["state"] == self.audit.state and status["written"] == len(
                    self.audit.files
                ):
                    return status
            except (FileNotFoundError, json.JSONDecodeError, PermissionError):
                pass
            time.sleep(0.001)
        raise TimeoutError("local v15 status writer did not settle")

    def make_probe(self, request):
        folder = self.control + "/" + request["id"]
        point = request["origin"]
        frames = []
        for i in range(3):
            name = f"native_{i}.rgb"
            self.files[folder + "/" + name] = bytes([i + 1]) * (512 * 288 * 3)
            frames.append(
                dict(file=name, origin=point, native_roi=True, source_frame_id=900 + i)
            )
        self.files[folder + "/overview.rgb"] = bytes(512 * 288 * 3)
        result = dict(
            ok=True,
            id=request["id"],
            region=dict(origin=point, size=[512, 288]),
            frames=frames,
            overview=dict(
                file="overview.rgb",
                size=[512, 288],
                source_size=[1920, 1080],
                native_roi=False,
            ),
        )
        self.files[folder + "/result.json"] = self.json_bytes(result)

    def get(self, path, local):
        self.downloads.append(path)
        with self.open(path) as stream:
            raw = stream.read()

        if "/dynamic_audit/" in path and path.endswith(".json"):
            data = json.loads(raw)
            if "corrupt_download_region" in self.faults:
                data["origin"] = [800, 360]
            if "corrupt_download_duplicate" in self.faults:
                data["source_frame_id"] = 1000
            raw = self.json_bytes(data)
        if (
            "/dynamic_audit/" in path
            and path.endswith(".rgb")
            and "corrupt_download_size" in self.faults
        ):
            raw = raw[:-1]
        Path(local).write_bytes(raw)

    def close(self):
        self.closed = True
        self.audit.close()


class SimulatedClient:
    def __init__(self, sftp):
        self.sftp = sftp
        self.closed = False

    def open_sftp(self):
        return self.sftp

    def close(self):
        self.closed = True
