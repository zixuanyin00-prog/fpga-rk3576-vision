from pathlib import Path
import json
import re
import threading
import time
import uuid
from environment_bridge import EnvironmentBridge, write
from sensor_device import source_after
from dynamic_audit import DynamicAudit
from training_capture_bridge import TrainingCaptureBridge


class CaptureLease:
    def __init__(self):
        self.lock = threading.RLock()
        self.owner = None

    def acquire(self, kind, ident):
        with self.lock:
            if self.owner is not None:
                return None
            token = (kind, ident, uuid.uuid4().hex)
            self.owner = token
            return token

    def release(self, token):
        with self.lock:
            if token != self.owner:
                raise RuntimeError("Capture lease owner mismatch")
            self.owner = None


class LeasedTrainingBridge(TrainingCaptureBridge):
    def __init__(self, root, lease):
        super().__init__(root)
        self.lease = lease

    def poll(self, camera, apply_region, busy=False):
        if not (self.folder / "request.json").exists():
            return False
        token = (
            None if busy else self.lease.acquire("training_control", "probe_or_region")
        )
        try:
            return super().poll(camera, apply_region, busy=token is None)
        finally:
            if token is not None:
                self.lease.release(token)


def admitted(spec, frame, context):
    if not spec.get("environment_profile_digest"):
        return True
    return (
        context.get("sensor_transition") is False
        and context.get("sensor_generation") == spec.get("expected_sensor_generation")
        and context.get("region_version") == spec.get("region_version")
        and context.get("boot_id") == spec.get("boot_id")
        and context.get("runtime_session_id") == spec.get("runtime_session_id")
        and context.get("isp_generation") == spec.get("isp_generation")
        and [frame.x, frame.y, 512, 288] == spec.get("native_roi")
        and type(spec.get("sensor_stable_source_id")) is int
        and source_after(frame.source_frame_id, spec["sensor_stable_source_id"])
    )


class LeasedDynamicAudit(DynamicAudit):
    def __init__(self, root, lease):
        super().__init__(root)
        self.lease = lease
        self.token = None
        self.deadline = 0
        self.seen = set()

    def _release(self):
        if self.token is not None:
            self.lease.release(self.token)
            self.token = None

    def _fail(self, reason):
        if self.request:
            folder = self.root / self.request["id"]
            folder.mkdir(exist_ok=True)
            write(folder / "failure.json", dict(error=reason, request=self.request))
        self.request = None
        self._release()

    def tick(self):
        if self.request and time.monotonic() > self.deadline:
            self._fail("Capture deadline exceeded")

    def offer(self, frame, objects, key, decisions, done, model_sha256, context=None):
        import queue

        context = context or {}
        now = time.monotonic()
        self.tick()
        if self.request is None and now >= self.next_poll:
            self.next_poll = now + 0.5
            path = self.root / "request.json"
            if path.exists():
                spec = json.loads(path.read_text())
                name = str(spec.get("id", ""))
                if not re.fullmatch(r"[A-Za-z0-9_]{1,80}", name):
                    raise ValueError("Invalid dynamic audit ID")
                if not 1 <= spec["frames"] <= 240 or not 0.2 <= spec["hz"] <= 5:
                    raise ValueError("Unbounded capture")
                self.token = self.lease.acquire("dynamic_audit", name)
                if self.token is None:
                    return
                try:
                    if (self.root / name).exists():
                        raise ValueError("Dynamic audit already exists")
                    path.unlink()
                    self.request = spec
                    self.offered = self.dropped = 0
                    self.seen = set()
                    self.next_sample = now
                    self.deadline = now + spec["frames"] / spec["hz"] + 60
                except Exception:
                    self._release()
                    raise
        if self.request is None or now < self.next_sample:
            return
        spec = self.request
        if not admitted(spec, frame, context):
            self._fail("Environment identity or stable source fence changed")
            return
        if frame.source_frame_id in self.seen:
            return
        self.next_sample = now + 1.0 / spec["hz"]
        metadata = dict(
            request=spec,
            index=self.offered,
            source_frame_id=frame.source_frame_id,
            origin=[frame.x, frame.y],
            completed_monotonic=done,
            model_sha256=model_sha256,
            primary_track=key,
            objects=objects,
            applied=decisions,
            dropped_before=self.dropped,
        )
        metadata.update(context)
        if spec.get("environment_profile_digest"):
            metadata["environment_profile_digest"] = spec["environment_profile_digest"]
        try:
            self.queue.put_nowait(
                (spec["id"], self.offered, frame.rgb.tobytes(), metadata)
            )
        except queue.Full:
            self.dropped += 1
            return
        self.seen.add(frame.source_frame_id)
        self.offered += 1
        if self.offered >= spec["frames"]:
            self.request = None
            self._release()

    def close(self):
        self._fail("Coordinator stopped before capture completed")
        super().close()


class CoordinatorEnvironment:
    def __init__(self, pipeline, config, boot_id):
        self.pipeline = pipeline
        self.lease = CaptureLease()
        self.boot_id = boot_id
        if (
            config.get("verified_boot_id") != boot_id
            or config.get("deployment_verified") is not True
        ):
            raise ValueError(
                "Environment identity must be verified against this boot before enabling"
            )
        for field in ("firmware_sha256", "driver_sha256"):
            if not re.fullmatch("[0-9a-f]{64}", config.get(field, "")):
                raise ValueError("Missing verified " + field)
        self.config = config
        self.session = uuid.uuid4().hex
        self.epoch = 0
        self.capability_verified = False
        self.bridge = EnvironmentBridge(
            pipeline.camera,
            pipeline.root,
            self.identity,
            self.lease.acquire,
            self.lease.release,
            self.on_state,
            state_lock=pipeline.lock,
            control_dispatch=pipeline.tracking_control,
        )
        self.on_state(
            dict(
                sensor_transition=True,
                sensor_state="unverified",
                sensor_generation=None,
            )
        )

        self.initial_readback = True

    @classmethod
    def create(cls, pipeline):
        path = pipeline.root / "camera_environment_identity.json"
        if not path.exists():
            return None
        return cls(
            pipeline,
            json.loads(path.read_text()),
            Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        )

    def identity(self):
        p = self.pipeline
        with p.lock:
            return dict(
                boot_id=self.boot_id,
                runtime_session_id=self.session,
                firmware_sha256=self.config["firmware_sha256"],
                driver_sha256=self.config["driver_sha256"],
                model_sha256=p.model_sha256,
                isp_generation=p.generation,
                region_version=p.tracker_region_version,
                origin=list(p.origin),
            )

    def on_state(self, state, bridge_commit=None):
        p = self.pipeline

        def apply():
            old = p.sensor_context
            changed = state.get("sensor_generation") != old.get(
                "sensor_generation"
            ) or (
                state.get("sensor_transition") is True
                and old.get("sensor_transition") is not True
            )
            if changed:
                reset = getattr(p.tracker, "reset_evidence", p.tracker.reset)
                reset()
                p.refresh_tracking_snapshot()
            with p.lock:
                if state.get("sensor_register_readback") is True:
                    self.capability_verified = True
                if changed:
                    self.epoch += 1
                    p.last_coarse_source = None
                    p.rows = []
                    p.last_time = 0.0
                    if hasattr(p, "_invalidate"):
                        p._invalidate()
                p.sensor_context = dict(
                    state, boot_id=self.boot_id, runtime_session_id=self.session
                )
                if bridge_commit is not None:
                    bridge_commit()

                self._sensor_snapshot = (self.epoch, p.sensor_context)
                p.result_event.set()

        return p.tracking_control(apply)

    def context(self, frame):

        p = self.pipeline
        epoch, state = self._sensor_snapshot
        value = dict(
            state,
            region_version=p.tracker_region_version,
            isp_generation=p.generation,
            sensor_epoch=epoch,
            fpga_epoch=frame.fpga_epoch,
            processing_revision=frame.processing_revision,
            enhance_revision=frame.enhance_revision,
        )
        fence = value.get("sensor_stable_source_id")
        if type(fence) is not int or not source_after(frame.source_frame_id, fence):
            value["sensor_transition"] = True
        return value

    def valid(self, context):
        return (
            context.get("sensor_epoch") == self.epoch
            and context.get("sensor_transition") is False
            and self.pipeline.sensor_context.get("sensor_transition") is False
        )

    def poll(self):

        p = self.pipeline
        if not p.lock.acquire(blocking=False):
            self.poll_deferred_tracking = getattr(self, "poll_deferred_tracking", 0) + 1
            return
        try:
            identity = self.identity()
        finally:
            p.lock.release()
        folder = self.bridge.folder
        if self.initial_readback:
            if (
                not (folder / "request.json").exists()
                and not (folder / "processing.json").exists()
            ):
                write(
                    folder / "request.json",
                    dict(
                        id="startup_" + self.session,
                        operation="readback",
                        identity=identity,
                    ),
                )
            self.initial_readback = False
        self.bridge.poll(identity_hint=identity, status_writer=p.status_writer)

    def close(self):
        self.bridge.cancel.set()
        if self.bridge.thread:
            self.bridge.thread.join(5)
            if self.bridge.thread.is_alive():
                raise RuntimeError("Environment control worker did not stop")
