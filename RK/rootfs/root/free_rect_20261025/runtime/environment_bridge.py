from pathlib import Path
import hashlib
import json
import queue
import re
import threading
import time
import uuid
from sensor_device import SensorSession, source_after


def write(path, value):
    path = Path(path)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temp.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temp.replace(path)


def telemetry(root):
    runtime = json.loads((root / "runtime_status.json").read_text())
    displays = {}
    for name in runtime["display_outputs"]:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise ValueError("Invalid output name")
        displays[name] = json.loads((root / f"display_{name}.json").read_text())
    now = float(Path("/proc/uptime").read_text().split()[0])
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    return dict(
        runtime=runtime,
        displays=displays,
        board_now=now,
        boot_id=boot,
        fresh=bool(displays)
        and all(
            -0.011 <= now - d["recorded_monotonic"] < 3
            for d in [runtime] + list(displays.values())
        ),
    )


class EnvironmentBridge:
    def __init__(
        self,
        camera,
        root,
        identity_provider,
        acquire_lease,
        release_lease,
        on_sensor_state,
        state_lock=None,
        control_dispatch=None,
    ):
        self.state_lock = state_lock or threading.RLock()
        self.control_dispatch = control_dispatch
        self.update_lock = threading.RLock()
        self.camera = camera
        self.root = Path(root)
        self.folder = self.root / "camera_environment"
        self.folder.mkdir(exist_ok=True)
        self.identity_provider = identity_provider
        self.acquire_lease = acquire_lease
        self.release_lease = release_lease
        self.on_sensor_state = on_sensor_state
        self.session = SensorSession(camera)
        self.queue = queue.Queue(maxsize=1)
        self.thread = None
        self.cancel = threading.Event()
        self.next_poll = 0
        self.collecting = False
        self.target_origin = None
        self.status = dict(sensor_transition=True, sensor_state="unverified")

    def state(self):
        with self.state_lock:
            return dict(self.status)

    def offer(self, frame, context):

        if not self.collecting or not frame.roi:
            return
        if (frame.x, frame.y) != tuple(self.target_origin):
            return
        try:
            self.queue.put_nowait((frame, dict(context)))
        except queue.Full:
            pass

    def poll(self, identity_hint=None, status_writer=None):
        now = time.monotonic()
        if now < self.next_poll:
            return
        self.next_poll = now + 0.25
        identity = (
            identity_hint if identity_hint is not None else self.identity_provider()
        )
        if status_writer is None:
            write(self.folder / "identity.json", identity)
        else:
            status_writer.offer(self.folder / "identity.json", identity)
        if self.thread and self.thread.is_alive():
            if (self.folder / "cancel").exists():
                self.cancel.set()
            return
        path = self.folder / "request.json"
        if not path.exists():
            return
        claimed = self.folder / "processing.json"
        if claimed.exists():
            return
        path.rename(claimed)
        self.cancel.clear()
        (self.folder / "cancel").unlink(missing_ok=True)
        self.thread = threading.Thread(
            target=self._run, args=(claimed,), name="camera-environment", daemon=True
        )
        self.thread.start()

    def update(self, state):
        state = dict(state)
        with self.update_lock:
            if self.control_dispatch is not None:

                self.control_dispatch(
                    lambda: self.on_sensor_state(
                        state, lambda: setattr(self, "status", state)
                    )
                )
            else:
                with self.state_lock:
                    self.status = state
                    self.on_sensor_state(state)
            write(self.folder / "sensor_status.json", state)

    def _run(self, path):
        lease = None
        out = None
        request = {}
        previous = None
        try:
            request = json.loads(path.read_text())
            ident = request.get("id", "")
            if not re.fullmatch(r"[A-Za-z0-9_]{1,80}", ident):
                raise ValueError("Invalid request ID")
            out = self.folder / ident
            out.mkdir(exist_ok=False)
            lease = self.acquire_lease("camera_environment", ident)
            if lease is None:
                raise RuntimeError(
                    "Another capture/region operation owns the coordinator"
                )
            identity = self.identity_provider()
            if request.get("identity") != identity:
                raise RuntimeError("Device or region identity changed before trial")
            operation = request["operation"]
            if operation == "trial":
                if request.get("phase") not in ("static", "motion"):
                    raise ValueError("Invalid phase")
                if request.get("origin") != identity["origin"]:
                    raise RuntimeError(
                        "Use the coordinator current region; this bridge cannot change it"
                    )

            trial_before = telemetry(self.root) if operation == "trial" else None
            if operation == "readback":
                self.update(
                    dict(
                        self.status,
                        sensor_readback_pending=True,
                        control_request_id=ident,
                    )
                )
            else:
                self.update(
                    dict(
                        sensor_transition=True,
                        sensor_state="submitted",
                        sensor_generation=self.status.get("sensor_generation"),
                        control_request_id=ident,
                        sensor_request_id=None,
                    )
                )
            if operation == "readback":
                sensor = self.session.readback(self.cancel.is_set)
            elif operation in ("trial", "apply"):

                def preserve_before(value):
                    nonlocal previous
                    previous = value
                    write(out / "before_sensor.json", value)

                previous, sensor = self.session.apply(
                    float(request["exposure_us"]),
                    int(request["gain_code"]),
                    self.cancel.is_set,
                    preserve_before,
                )
            elif operation == "restore":
                previous = request["restore"]
                sensor = self.session.wait(
                    self.session.request(
                        int(previous["shr0"]), int(previous["gain_code"]), 2
                    ),
                    self.cancel.is_set,
                )
            else:
                raise ValueError("Unsupported environment operation")
            self.update(sensor)
            write(out / "sensor.json", sensor)
            if operation != "trial":
                write(
                    out / "result.json",
                    dict(ok=True, id=ident, sensor=sensor, identity=identity),
                )
                return
            origin = request["origin"]
            self.session.call("quality_origin", origin)
            before = trial_before
            start = time.monotonic()
            frames = []
            rejections = {}
            seen = set()
            last = 0
            self.target_origin = origin
            while not self.queue.empty():
                self.queue.get_nowait()
            self.collecting = True
            while time.monotonic() - start < 33 or len(frames) < 8:
                if self.cancel.is_set():
                    raise RuntimeError(
                        "Cancelled; sensor state must be re-read before further sampling"
                    )
                if time.monotonic() - start > 45:
                    raise TimeoutError(
                        "Not enough stable, distinct native frames with matching FPGA statistics"
                    )
                if self.identity_provider() != identity:
                    raise RuntimeError("Region/ISP/model/boot changed during trial")
                try:
                    frame, context = self.queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                if len(frames) >= 8 or time.monotonic() - last < 0.5:
                    continue
                last = time.monotonic()
                reason = None
                if (
                    context.get("region_version") != identity["region_version"]
                    or context.get("boot_id") != identity["boot_id"]
                ):
                    reason = "frame_identity_mismatch"
                elif (
                    context.get("sensor_generation") != sensor["sensor_generation"]
                    or context.get("sensor_transition") is not False
                ):
                    reason = "transition_or_generation"
                elif frame.source_frame_id in seen or not source_after(
                    frame.source_frame_id, sensor["sensor_stable_source_id"]
                ):
                    reason = "duplicate_or_transition_source"
                q = self.session.call("quality_snapshot")
                if (
                    q["source_frame_id"] != frame.source_frame_id
                    or q["origin"] != origin
                ):
                    reason = "unmatched_quality_frame"
                if q["fault"] or q["pixels"] != 512 * 288:
                    reason = "invalid_quality"
                if reason:
                    rejections[reason] = rejections.get(reason, 0) + 1
                    continue

                current = self.session.status()
                if (
                    current["sensor_transition"]
                    or current["sensor_generation"] != sensor["sensor_generation"]
                ):
                    raise RuntimeError("Sensor state changed")
                raw = frame.rgb.tobytes()
                if len(raw) != 512 * 288 * 3:
                    raise RuntimeError("Invalid native frame size")
                name = f"native_{len(frames)}.rgb"
                (out / name).write_bytes(raw)
                frames.append(
                    dict(
                        file=name,
                        source_frame_id=frame.source_frame_id,
                        origin=origin,
                        sha256=hashlib.sha256(raw).hexdigest(),
                        quality=q,
                        sensor_generation=sensor["sensor_generation"],
                        sensor_transition=False,
                        objects=[],
                        reviewed=False,
                    )
                )
                seen.add(frame.source_frame_id)
            after = telemetry(self.root)
            result = dict(
                ok=True,
                id=ident,
                phase=request["phase"],
                identity=identity,
                origin=origin,
                sensor=sensor,
                frames=frames,
                telemetry_before=before,
                telemetry_after=after,
                environment_digest=request["environment_digest"],
                rejections=rejections,
                pixels_transformed_on_cpu=False,
                quality_producer="fpga",
            )
            write(out / "result.json", result)
        except Exception as e:
            self.update(
                dict(
                    self.status,
                    sensor_transition=True,
                    sensor_state="error",
                    error_message=str(e),
                )
            )
            if out is not None:
                write(
                    out / "result.json",
                    dict(
                        ok=False,
                        id=request.get("id"),
                        error=str(e),
                        previous_sensor=previous,
                        recovery="Read back first; restore saved SHR0/gain with explicit restore operation if needed",
                    ),
                )
        finally:
            self.collecting = False
            if lease is not None:
                self.release_lease(lease)
            path.unlink(missing_ok=True)
