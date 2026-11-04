import unrecognized_capture
import object_outcomes
from sequence_capture import SequenceCapture
from action_output import ActionClient
import full_roi_runtime
import motion_wire
import native_tracking
import native_count_store
import os

MINIMAL60 = os.environ.get("VISION_MINIMAL60") == "1"
if MINIMAL60:
    import minimal_metrics as native_metrics
else:
    import native_metrics
import runtime_timing
import console_native
import pipeline_control_io
import async_status_storage

if MINIMAL60:
    from tracking_shared import SharedTracker as RemoteTracker
else:
    from tracking_rpc import RemoteTracker
"""Manual native rectangle -> one fine model -> geometry IDs and discrete poses.

The DMA owner and preview never wait for NPU, PNG encoding, or control storage.
"""
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
from collections import deque
import pickle
import hashlib
import json
import threading
import time
import uuid
import queue
import os
from concurrent.futures import Future
from full_roi_runtime import OrderedWholeFrames as OrderedInference
import numpy as np
from PIL import Image, ImageDraw
from rectangles import rectangle, settings, contains
from decoder import decode, iou

WEB_CAPTURE_PROTOCOL = "native-rectangle-v3"
WEB_CAPTURE_SOURCE = "rk_raw_preview_native_crop"


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    for attempt in range(6):
        try:
            temp.replace(path)
            break
        except PermissionError:
            if os.name != "nt" or attempt == 5:
                raise
            time.sleep(0.005 * (attempt + 1))


def model_input(rgb, config=None):

    config = config or {"input_transform": "stretch_bilinear"}
    if config.get("input_size", [512, 288]) != [512, 288]:
        raise ValueError("Unsupported decoder/model input geometry")
    if rgb.shape == (288, 512, 3):
        return np.ascontiguousarray(rgb)
    if config.get("input_transform") != "stretch_bilinear":
        raise ValueError("Selected model requires native 512x288")
    return np.asarray(
        Image.fromarray(rgb).resize((512, 288), Image.Resampling.BILINEAR)
    ).copy()


class Tracks:
    def __init__(self):
        self.tracks = {}
        self.region_version = uuid.uuid4().hex
        self.next_id = 0
        self.last_source = None
        self.counts = {}

    def reset(self):
        object_outcomes.interrupt(self, "configuration_changed")
        self.tracks.clear()
        self.region_version = uuid.uuid4().hex
        self.last_source = None

    def reset_evidence(self):

        object_outcomes.interrupt(self, "sensor_changed")
        self.tracks.clear()
        self.last_source = None

    def clear_counts(self):
        native_tracking.reset_counters(self)
        self.reset_evidence()

    def observe(self, objects, source, now, hits, threshold):
        return native_tracking.observe_tracks(
            self, objects, source, now, hits, threshold
        )


class RectPipeline:
    def __init__(
        self,
        camera,
        root,
        selection=None,
        engine_factory=None,
        autostart=True,
        result_sink=None,
    ):
        self.result_sink = result_sink
        self.display_revision = 0
        if result_sink:
            result_sink.invalidate_results(self.display_revision)
        self.tracking_control_lock = threading.RLock()
        self.root = Path(root)
        self.boot_id = (
            Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            if os.name != "nt"
            else "offline"
        )
        self.camera = camera
        self.selection = selection
        self.lock = pipeline_control_io.StateLock()
        self.status_writer = async_status_storage.Writer()
        self.control_io_lock = threading.RLock()
        self.control_io_active = False
        self.control_io_revision = 0
        self.control_io_depth = 0
        self.control_io_transactions = 0
        self.control_io_started = 0.0
        self.control_io_last_ms = 0.0
        self.control_io_max_ms = 0.0
        self.sample_lock = threading.Lock()
        self.capture_lock = threading.Lock()
        self.stop = threading.Event()
        self.result_event = threading.Event()
        self.error = None
        self._tracking_started = False
        self.generation = 0
        self.control = SimpleNamespace(
            scheduler=SimpleNamespace(tracks={}), collect=lambda wait=True: None
        )
        self.session = uuid.uuid4().hex
        self.action_output = ActionClient()
        self.action_send_timing = (None, None, None, None)
        self.tracker = Tracks()
        self.tracker_region_version = self.tracker.region_version
        self.count_session = uuid.uuid4().hex
        self.counter = SimpleNamespace(
            save=lambda asynchronous=False: native_count_store.save(self, asynchronous),
            reset=self.reset_counts,
        )
        self.settings = settings(
            json.loads((self.root / "demo_config.json").read_text(encoding="utf-8"))
        )
        self.version = uuid.uuid4().hex
        self.origin = tuple(self.settings["rectangle"][:2])
        self.rows = []
        self.last_time = 0
        self.next_status = 0
        self.count_stage_snapshot = {}
        self.sensor_context = {}
        self.last_coarse_source = None
        self.model_sha256 = selection["fine_sha256"] if selection else "0" * 64
        self.engine = None
        self.engines = []
        self.config = None
        self.latest_native = None
        self.recent = deque(maxlen=240)
        self.roi_recent = deque(maxlen=240)
        self.image_identity = None
        self.minimal60 = MINIMAL60
        sample_window = 120 if MINIMAL60 else 2048
        self.scheduler = None
        self.capture_commands = queue.Queue(maxsize=4)
        self.last_admitted = None
        self.capture_count = 0
        self.capture_wait = deque(maxlen=sample_window)
        self.core_samples = [deque(maxlen=sample_window), deque(maxlen=sample_window)]
        self.core_calls = [0, 0]
        self.published_count = 0
        self.stale_results = 0
        self.ordered_latency = deque(maxlen=sample_window)
        self.trace = deque(maxlen=sample_window)
        self.metric_cache = {}
        self.metric_due = 0
        self.worker_scheduling = {}
        self.test_engine = engine_factory is not None
        self.process_inference = False
        self.applied_configuration = None
        self.effective_source = None
        self.effective_source_gaps = 0
        self.duplicate_results = 0
        self.queue = queue.Queue(maxsize=1)
        self.failure_dropped = 0
        self.folder = self.root / "rect_control"
        self.folder.mkdir(exist_ok=True)
        interrupted = self.folder / "processing.json"
        if interrupted.exists():
            try:
                pending = json.loads(interrupted.read_text(encoding="utf-8"))
                ident = pending.get("id", "")
                if isinstance(ident, str) and ident.isalnum() and len(ident) <= 64:
                    result = self.folder / ident / "result.json"
                    if not result.exists():
                        write(
                            result,
                            dict(
                                ok=False,
                                id=ident,
                                error="Runtime restarted during operation; refresh before retry",
                            ),
                        )
            except (ValueError, TypeError):
                pass
            interrupted.rename(
                self.folder / ("interrupted." + uuid.uuid4().hex + ".json")
            )
        self.environment = None
        self.sequence_capture = SequenceCapture()
        from coordinator_environment import CoordinatorEnvironment

        if (self.root / "camera_environment_identity.json").exists():
            self.environment = CoordinatorEnvironment.create(self)
        if selection:
            if not self.environment and engine_factory is None:
                raise ValueError(
                    "Verified camera identity required before model initialization"
                )
            self.config = json.loads(
                (
                    Path(selection["fine_config_path"])
                    if selection and selection.get("fine_config_path")
                    else self.root / "model/config.json"
                ).read_text(encoding="utf-8")
            )
            full_roi_runtime.validate_model_config(self.config)
            self.process_inference = (
                MINIMAL60
                and engine_factory is None
                and os.environ.get("VISION_RKNN_C_API") == "1"
            )
            init_affinity = None
            if engine_factory is None:
                init_affinity = os.sched_getaffinity(0)
                os.sched_setaffinity(0, {6} if MINIMAL60 else {6, 7})
            try:
                for core in (0, 1):
                    if engine_factory:
                        engine = engine_factory(
                            Path(selection["fine_model_path"])
                            if selection and selection.get("fine_model_path")
                            else self.root / "model/detector_fp16.rknn"
                        )
                    else:
                        if self.process_inference:
                            from native_inference_process import (
                                NativeInferenceProcess as RKNNLite,
                            )

                            engine = RKNNLite(self.config, selection)
                        else:
                            if os.environ.get("VISION_RKNN_C_API") == "1":
                                from native_engine import NativeEngine as RKNNLite
                            else:
                                from rknnlite.api import RKNNLite
                            engine = RKNNLite(
                                verbose=False,
                                **(
                                    {"reuse_outputs": True}
                                    if os.environ.get("VISION_RKNN_C_API") == "1"
                                    else {}
                                ),
                            )
                        self.engines.append(engine)
                        if engine.load_rknn(
                            str(
                                Path(selection["fine_model_path"])
                                if selection and selection.get("fine_model_path")
                                else self.root / "model/detector_fp16.rknn"
                            )
                        ) or engine.init_runtime(
                            core_mask=(RKNNLite.NPU_CORE_0, RKNNLite.NPU_CORE_1)[core]
                        ):
                            raise RuntimeError("Fine model initialization failed")
                    if engine_factory:
                        self.engines.append(engine)
            except BaseException:
                for engine in self.engines:
                    engine.release()
                raise
            finally:
                if init_affinity is not None:
                    os.sched_setaffinity(0, init_affinity)
            self.engine = self.engines[0]
            self.scheduler = OrderedInference(
                self._infer, self._publish, self._fail, setup=self._worker_setup
            )
            if MINIMAL60:
                self.scheduler.phase_samples = deque(maxlen=120)
        self.acquisition_source = (
            "raw_preview_native_crop"
            if self.engine is None
            and os.environ.get("VISION_PREVIEW_CODEC", "raw") == "raw"
            else "fpga_roi_channel1"
        )
        native_count_store.restore(self, selection)
        local = self.tracker
        ledger = native_tracking.counters(local)
        restore_record = dict(
            passage_ledger={
                key: ledger[key]
                for key in (
                    "counting_semantics_version",
                    "pending",
                    "final_unconfirmed",
                    "identity_lost_unconfirmed",
                    "identity_lost_confirmed",
                    "interrupted_unconfirmed",
                    "legacy_unresolved",
                )
            }
        )
        self.tracker = RemoteTracker(
            initial_state=dict(
                counts=dict(local.counts),
                observed_total=ledger["observed_total"],
                restore_record=restore_record,
                region_version=local.region_version,
                next_id=local.next_id,
            ),
            parent_context=self.tracking_context(),
        )
        self.refresh_tracking_snapshot()
        write(
            self.folder / "protocol.json",
            dict(
                protocol=WEB_CAPTURE_PROTOCOL,
                sensor_size=[1920, 1080],
                single_source_frame=True,
                max_pixels=518400,
                capture_source=WEB_CAPTURE_SOURCE,
                source_channel=0,
                source_size=[1920, 1080],
                web_capture_protocol=WEB_CAPTURE_PROTOCOL,
                web_capture_source=WEB_CAPTURE_SOURCE,
                processing="shared_optional_rgb_v2",
            ),
        )
        self.writer = threading.Thread(target=self._writer, daemon=True)
        self.writer.start()
        self.thread = threading.Thread(
            target=self._run, name="rect-coordinator", daemon=True
        )
        self.control_thread = threading.Thread(
            target=self._control_loop, name="rect-control", daemon=True
        )
        if autostart:
            self.start()

    def tracking_control(self, callback):

        scheduler = getattr(self, "scheduler", None)
        if not self._tracking_started or scheduler is None:
            with self.tracking_control_lock:
                return callback()
        if scheduler.is_collector_thread():
            return callback()
        if self.lock._is_owned():
            raise RuntimeError("Tracker control must not wait under the state lock")
        return scheduler.call_control(callback).result(timeout=8.0)

    def tracking_context(self, **changes):
        identity = self.environment.identity() if self.environment else {}
        metadata = dict(
            boot_id=identity.get("boot_id", self.boot_id),
            runtime_session_id=self.session,
            counting_session=self.count_session,
            configuration_version=self.version,
            model_sha256=self.model_sha256,
            run=self.selection.get("run") if self.selection else None,
            pid=os.getpid(),
        )
        metadata.update(changes)
        return metadata

    def make_tracking_snapshot(self):

        if isinstance(self.tracker, RemoteTracker):
            return self.tracker.snapshot()

        return pickle.loads(
            pickle.dumps(
                dict(
                    class_counts=dict(self.tracker.counts),
                    ledger=native_tracking.counters(self.tracker),
                    region_version=self.tracker.region_version,
                    active_tracks=len(self.tracker.tracks),
                ),
                protocol=5,
            )
        )

    def refresh_tracking_snapshot(self, update=None):

        view = self.make_tracking_snapshot()
        with self.lock:
            self.tracker_region_version = view["region_version"]
            if update is not None:
                update()
            self.tracking_snapshot = view
        return view

    def control_io_transaction(self):
        return pipeline_control_io.transaction(self)

    def start(self):
        native_metrics.start(self)
        if self.scheduler:
            self.scheduler.start()
            self._tracking_started = True
        self.thread.start()
        self.control_thread.start()

    def reset_counts(self, expected_session=None):
        def apply():
            if expected_session is not None and expected_session != self.count_session:
                raise ValueError("Counting session changed; refresh before resetting")
            previous = dict(
                session_id=self.count_session,
                class_counts=dict(self.tracker.counts),
                recorded_monotonic=time.monotonic(),
            )
            previous.update(self.tracker.counters())
            self.status_writer.offer(
                self.root / "count_resets" / (self.count_session + ".json"), previous
            )
            self.tracker.reset("counts")
            new_session = uuid.uuid4().hex
            self.tracker.bind(self.tracking_context(counting_session=new_session))

            def commit():
                self.rows = []
                self.last_time = 0.0
                self.count_session = new_session
                self.counter_history_scope = "current_session"
                self._invalidate()
                self.result_event.set()

            self.refresh_tracking_snapshot(commit)
            return self.count_session

        return self.tracking_control(apply)

    @property
    def generation(self):
        return self._generation

    @generation.setter
    def generation(self, value):
        if not hasattr(self, "tracker"):
            self._generation = value
            return

        def apply():
            if self._generation == value:
                return
            self.tracker.reset("evidence")

            def commit():
                self._generation = value
                self.rows = []
                self.last_time = 0.0
                self._invalidate()
                self.result_event.set()

            self.refresh_tracking_snapshot(commit)

        self.tracking_control(apply)

    def _writer(self):
        return console_native.write_failures(self)

    def _invalidate(self):
        self.action_output.invalidate()
        self.count_stage_snapshot = {}
        self.display_revision += 1
        if self.result_sink:
            self.result_sink.invalidate_results(self.display_revision)
        self.last_admitted = None
        self.effective_source = None
        if getattr(self, "scheduler", None):
            self.scheduler.invalidate()
        if hasattr(self, "queue"):
            while True:
                try:
                    self.queue.get_nowait()
                except queue.Empty:
                    break

            self.status_writer.remove(self.root / "demo_failure/latest.json")

    def _token(self):
        return (
            self.version,
            self.generation,
            self.count_session,
            (
                self.environment.epoch if self.environment else 0,
                self.control_io_revision,
                self.image_identity,
            ),
        )

    def _fail(self, exc):
        self.error = exc
        self.stop.set()
        self.result_event.set()

    def _worker_setup(self, core):
        if not self.test_engine:
            from capture_scheduling import apply_current_thread

            self.worker_scheduling[str(core)] = apply_current_thread(
                (6,) if MINIMAL60 else (6, 7), 0, "fine-core" + str(core)
            )

    def _capture(self, rect, last_source=None, *, fence=None):
        x, y, w, h = rectangle(rect)
        frame = self.camera.preview_after(last_source, fence=fence)
        if (
            frame.channel != 0
            or frame.roi
            or (frame.x, frame.y, frame.rgb.shape) != (0, 0, (1080, 1920, 3))
        ):
            raise ValueError(
                "Web capture requires the verified full-RAW channel0 cache"
            )

        return replace(frame, x=x, y=y, rgb=frame.rgb[y : y + h, x : x + w])

    def _acquire_native(self, rect):
        x, y, w, h = rectangle(rect)
        start = time.monotonic()
        if self.acquisition_source == "raw_preview_native_crop":

            _, frame = self.camera.acquire(False)
            frame = replace(
                frame, roi=True, x=x, y=y, rgb=frame.rgb[y : y + h, x : x + w]
            )
        else:
            _, frame = self.camera.acquire(True, x, y, width=w, height=h)
        if not frame.roi or (frame.x, frame.y, frame.rgb.shape) != (x, y, (h, w, 3)):
            raise ValueError("Native rectangle metadata mismatch")
        now = time.monotonic()
        with self.capture_lock:
            self.latest_native = frame
            self.roi_recent.append(now)
            self.capture_count += 1
            self.capture_wait.append((now, (now - start) * 1000))
        return frame

    def _control_loop(self):
        try:
            while not self.stop.is_set():
                self._control()
                self.stop.wait(0.01)
        except BaseException as exc:
            self._fail(exc)

    def _control(self):
        path = self.folder / "request.json"
        if not path.exists():
            return
        claimed = self.folder / "processing.json"
        path.rename(claimed)
        req = {}
        out = None
        lease = None
        try:
            req = json.loads(claimed.read_text(encoding="utf-8"))
            ident = req["id"]
            if not isinstance(ident, str) or not ident.isalnum() or len(ident) > 64:
                raise ValueError("Invalid transaction ID")
            out = self.folder / ident
            if out.exists():
                out = None
                raise ValueError("Transaction already exists")
            out.mkdir()
            if req.get("expected_version") != self.version:
                raise ValueError("Runtime/configuration changed; refresh first")
            op = req["operation"]
            if self.environment:
                lease = self.environment.lease.acquire("rectangle_control", ident)
                if lease is None:
                    raise ValueError(
                        "Camera settings are being changed; retry after completion"
                    )
            if op == "capture_sequence":
                metadata = dict(
                    version=self.version,
                    model_sha256=self.model_sha256,
                    selection=self.selection,
                    settings=dict(self.settings),
                    identity=self.environment.identity(),
                    labels=self.config["labels"],
                    titles=self.config.get("titles", self.config["labels"]),
                    pid=os.getpid(),
                    counting_before=self.tracking_snapshot["ledger"],
                )
                capture = self.sequence_capture.start(
                    out, req.get("seconds", 3), metadata, pixels=req.get("pixels", True)
                )
                result = dict(ok=True, id=ident, **capture)
            elif op == "configure":
                config = settings(req["values"])
                full_roi_runtime.capture_rectangle(config, self.config)
                if (
                    self.engine
                    and self.config.get("input_transform")
                    not in (
                        "stretch_bilinear",
                        "native_tiles_v1",
                        "native_full_roi_fpn_integer_crop_v1",
                    )
                    and config["rectangle"][2:] != [512, 288]
                ):
                    raise ValueError(
                        "Selected model requires 512x288; train a free-rectangle model first"
                    )
                future = Future()
                self.capture_commands.put(
                    ("configure", (config, req["expected_version"]), future), timeout=2
                )
                ack = future.result(timeout=8)
                result = dict(ok=True, id=ident, **ack)
            elif op == "reset_counts":
                expected = req.get("expected_session_id")
                if not expected:
                    raise ValueError("Counting session is required")
                session = self.reset_counts(expected)
                result = dict(
                    ok=True, id=ident, session_id=session, version=self.version
                )
            elif op == "capture":
                rect = rectangle(req["rectangle"])
                count = req["frames"]
                hz = req.get("hz", 2)
                if (
                    type(count) is not int
                    or not 1 <= count <= 15
                    or type(hz) not in (int, float)
                    or not 0.2 <= hz <= 2
                ):
                    raise ValueError("Unbounded capture")
                before = self.environment.identity() if self.environment else None
                if before is None:
                    raise ValueError("Verified camera identity required")
                frames = []
                seen = set()
                hashes = set()
                processing = None
                deadline = time.monotonic() + count / hz + 15
                fence = self.environment.bridge.state().get("sensor_stable_source_id")
                if type(fence) is not int:
                    raise ValueError("Verified camera source fence required")
                preview = self.camera.latest
                if preview is None:
                    preview = self.camera.preview_after(fence=fence)
                (out / "overview.rgb").write_bytes(preview.rgb.tobytes())
                last_source = preview.source_frame_id
                for attempt in range(count * 3):
                    if self.stop.is_set() or time.monotonic() > deadline:
                        break
                    frame = self._capture(rect, last_source, fence=fence)
                    last_source = frame.source_frame_id
                    context = self.environment.context(frame)
                    if (
                        not self.environment.valid(context)
                        or self.environment.identity() != before
                    ):
                        raise ValueError("Camera identity changed during capture")
                    if frame.processing_config is None:
                        raise ValueError(
                            "Frame processing revision has no exact configuration"
                        )
                    effective = (
                        frame.fpga_epoch,
                        frame.processing_revision,
                        frame.enhance_revision,
                    )
                    if processing is not None and processing != effective:
                        raise ValueError(
                            "Effective image processing changed during capture"
                        )
                    processing = effective
                    raw = frame.rgb.tobytes()
                    sha = hashlib.sha256(raw).hexdigest()
                    if frame.source_frame_id not in seen and sha not in hashes:
                        name = f"native_{len(frames)}.rgb"
                        (out / name).write_bytes(raw)
                        frames.append(
                            dict(
                                file=name,
                                source_frame_id=frame.source_frame_id,
                                sha256=sha,
                                rectangle=rect,
                                identity=before,
                                origin=rect[:2],
                                source_size=[1920, 1080],
                                capture_source=WEB_CAPTURE_SOURCE,
                                channel=0,
                                source_channel=0,
                                source_request_id=frame.request_id,
                                capture_transaction_id=ident,
                                fpga_epoch=frame.fpga_epoch,
                                processing_revision=frame.processing_revision,
                                enhance_revision=frame.enhance_revision,
                                processing_config=frame.processing_config,
                                image_mode=frame.processing_config["image_mode"],
                                capture_received_monotonic=frame.capture_received_monotonic,
                                transport_dequeued_monotonic=frame.transport_dequeued_monotonic,
                                python_copy_returned_monotonic=frame.python_copy_returned_monotonic,
                            )
                        )
                        seen.add(frame.source_frame_id)
                        hashes.add(sha)
                    if len(frames) >= count:
                        break
                    self.stop.wait(1 / hz)
                result = dict(
                    ok=len(frames) == count,
                    id=ident,
                    protocol=WEB_CAPTURE_PROTOCOL,
                    rectangle=rect,
                    frames=frames,
                    processing="shared_optional_rgb_v2",
                    capture_source=WEB_CAPTURE_SOURCE,
                    source_channel=0,
                    source_size=[1920, 1080],
                    identity=before,
                    requested_frames=count,
                    terminal_status="complete" if len(frames) == count else "exhausted",
                    error=(
                        ""
                        if len(frames) == count
                        else "采集不足，保留已采原图；请改变工件位置后重试"
                    ),
                )
            else:
                raise ValueError("Unknown operation")
        except Exception as exc:
            result = dict(ok=False, id=req.get("id"), error=str(exc))
        finally:
            if lease is not None:
                self.environment.lease.release(lease)
            if out:
                write(out / "result.json", result)
            claimed.unlink(missing_ok=True)

    def _run(self):
        try:
            while not self.stop.is_set():
                if self.environment:
                    self.environment.poll()
                try:
                    operation, value, future = self.capture_commands.get_nowait()
                except queue.Empty:
                    operation = value = future = None
                if operation == "configure":
                    config, expected = value

                    def apply_configuration():
                        if expected != self.version:
                            return False
                        self.tracker.reset("region")
                        new_version = uuid.uuid4().hex
                        self.tracker.bind(
                            self.tracking_context(configuration_version=new_version)
                        )

                        def commit():
                            self.settings = config
                            self.version = new_version
                            self.origin = tuple(config["rectangle"][:2])
                            self.rows = []
                            self.last_time = 0.0
                            self._invalidate()

                        self.refresh_tracking_snapshot(commit)
                        return True

                    if not self.tracking_control(apply_configuration):
                        future.set_exception(
                            ValueError("Configuration changed before apply")
                        )
                        continue
                    write(self.root / "demo_config.json", config)

                config = dict(self.settings)
                rect = full_roi_runtime.capture_rectangle(config, self.config)
                frame = self._acquire_native(rect)
                effective = (
                    frame.fpga_epoch,
                    frame.processing_revision,
                    frame.enhance_revision,
                )
                if effective != self.image_identity:

                    def apply_image_identity():
                        if self.image_identity is not None:
                            self.tracker.reset("evidence")

                        def commit():
                            self.image_identity = effective
                            self.rows = []
                            self.last_time = 0.0
                            self._invalidate()

                        self.refresh_tracking_snapshot(commit)

                    self.tracking_control(apply_image_identity)
                token = self._token()
                acquired_monotonic = time.monotonic()
                context = self.environment.context(frame) if self.environment else {}
                self.sequence_capture.capture(frame, context, token[0])
                if self.environment:
                    self.environment.bridge.offer(frame, context)
                if (
                    rect == full_roi_runtime.capture_rectangle(config, self.config)
                    and token[0] == self.version
                ):
                    if (
                        self.applied_configuration is None
                        or self.applied_configuration["version"] != token[0]
                    ):
                        self.applied_configuration = dict(
                            version=token[0],
                            settings=config,
                            applied_source_frame_id=frame.source_frame_id,
                            applied_rectangle=rect,
                            capture_received_monotonic=frame.capture_received_monotonic,
                        )
                if future:
                    future.set_result(dict(self.applied_configuration))
                job = dict(
                    frame=frame,
                    config=config,
                    token=token,
                    context=context,
                    acquired_monotonic=acquired_monotonic,
                )
                admitted = False
                if (
                    self.scheduler
                    and rect == full_roi_runtime.capture_rectangle(config, self.config)
                    and self._valid(job)
                ):
                    source = frame.source_frame_id
                    if (
                        self.last_admitted is None
                        or 0 < ((source - self.last_admitted) & 0xFFFFFFFF) < 0x80000000
                    ):
                        self.last_admitted = source
                        admitted = True
                if admitted:
                    self.scheduler.submit(job)
        except BaseException as exc:
            if "future" in locals() and future and not future.done():
                future.set_exception(exc)
            self._fail(exc)
        finally:
            while not self.capture_commands.empty():
                try:
                    _, _, pending = self.capture_commands.get_nowait()
                    pending.set_exception(RuntimeError("Acquisition stopped"))
                except queue.Empty:
                    break

    def _valid(self, job):
        return (
            not self.control_io_active
            and job["token"] == self._token()
            and job["config"] == self.settings
            and (not self.environment or self.environment.valid(job["context"]))
        )

    def _infer(self, core, job):
        if self.process_inference:
            if not self._valid(job):
                self.stale_results += 1
                return None
            response = self.engines[core].execute(core, job)
            with self.sample_lock:
                self.core_samples[core].extend(response["core_samples"])
                self.core_calls[core] += response["model_calls"]

            if not self._valid(job):
                self.stale_results += 1
                return None
            return response["objects"], response["sample"]
        return full_roi_runtime.infer(self, core, job)

    def _publish(self, job, result):
        frame = job["frame"]
        config = job["config"]
        context = job["context"]
        version, generation, _, _ = job["token"]
        objects, sample = result
        publish_entered = time.monotonic()
        now = publish_entered
        with self.lock:
            if not self._valid(job):
                self.stale_results += 1
                return
            source = frame.source_frame_id
            if self.effective_source is not None:
                step = (source - self.effective_source) & 0xFFFFFFFF
                if not 0 < step < 0x80000000:
                    self.duplicate_results += 1
                    return
            else:
                step = 1
            previous_counts = self.tracking_snapshot["class_counts"]
            previous_stages = self.count_stage_snapshot
        validated = time.monotonic()
        tracking_wall = time.monotonic()
        tracking_cpu = time.thread_time()

        evidence_arguments = {}
        if MINIMAL60:
            hardware = self.environment.config if self.environment else {}
            evidence_arguments["failure_event_context"] = dict(
                boot_id=self.boot_id,
                runtime_session_id=self.session,
                counting_session=self.count_session,
                configuration_version=version,
                model_sha256=self.model_sha256,
                run=self.selection.get("run"),
                config_sha256=self.selection.get("config_sha256"),
                firmware_sha256=hardware.get("firmware_sha256"),
                driver_sha256=hardware.get("driver_sha256"),
                sensor={
                    key: context.get(key)
                    for key in (
                        "shr0",
                        "gain_code",
                        "vmax",
                        "hmax",
                        "sensor_generation",
                        "sensor_epoch",
                        "isp_generation",
                    )
                },
                processing_config=frame.processing_config,
                base_isp_values_available=False,
                metadata_scope="source-bound descriptor and admission context; base ISP values not exported per frame",
            )
        rows, failures, view, rpc = self.tracker.observe(
            frame,
            objects,
            source,
            now,
            config["track_hits"],
            config["confidence_percent"] / 100,
            **evidence_arguments,
        )
        tracking_finished = time.monotonic()

        for obj in objects:
            obj.pop("_real_pixel_features", None)
        sample = dict(
            sample,
            tracking_wall_ms=(tracking_finished - tracking_wall) * 1000,
            tracking_cpu_ms=rpc.get("local_cpu_ms", rpc["child_cpu_ms"]),
            tracking_parent_cpu_ms=(time.thread_time() - tracking_cpu) * 1000,
            tracking_total_cpu_ms=rpc["child_cpu_ms"]
            + (time.thread_time() - tracking_cpu) * 1000,
            tracking_child_wall_ms=rpc["child_wall_ms"],
            tracking_rpc_wall_ms=rpc["observe_rpc_wall_ms"],
            tracking_frame_pack_cpu_ms=rpc["parent_frame_pack_cpu_ms"],
            tracking_request_bytes=rpc["request_bytes"],
            tracking_process_pid=rpc["child_pid"],
            active_track_count=view["active_tracks"],
            publish_entered_monotonic=publish_entered,
            tracking_started_monotonic=tracking_wall,
            tracking_finished_monotonic=tracking_finished,
            publish_lock_wait_ms=(validated - publish_entered) * 1000,
        )

        copied = getattr(frame, "python_copy_returned_monotonic", None) or None
        acquired = job.get("acquired_monotonic")
        admitted = job.get("scheduler_admitted_monotonic")
        worker_started = job.get("npu_worker_started_monotonic")
        worker_finished = job.get("npu_worker_finished_monotonic")
        stage_boundaries = (
            ("python_queue_wait_ms", copied, acquired),
            ("admission_wait_ms", acquired, admitted),
            ("worker_dispatch_wait_ms", admitted, worker_started),
            ("npu_worker_wall_ms", worker_started, worker_finished),
            ("result_completed_to_collector_ms", worker_finished, publish_entered),
            ("result_to_collector_ms", sample.get("time"), publish_entered),
        )
        sample.update(
            {
                name: (
                    (ended - started) * 1000
                    if started is not None and ended is not None
                    else None
                )
                for name, started, ended in stage_boundaries
            }
        )
        association_uncertain_ids = {
            f["track_id"] for f in failures if f["reason"] == "association_uncertain"
        }
        for row in rows:
            row["display_motion_v1"] = motion_wire.freeze(
                row.get("display_motion_v1"), source
            )
            row.update(
                session_id=self.session,
                generation=generation,
                region_version=self.tracker.region_version,
                capture_received_monotonic=frame.capture_received_monotonic,
                observed_source_frame_id=source,
                sensor_transition=context.get("sensor_transition", False),
                sensor_generation=context.get("sensor_generation"),
                boot_id=context.get("boot_id"),
                association_uncertain=row["track_id"] in association_uncertain_ids,
            )
        failures = [] if MINIMAL60 else unrecognized_capture.select_rows(rows)
        frozen_at = time.monotonic()
        view = self.make_tracking_snapshot()
        sample["tracking_snapshot_ms"] = (time.monotonic() - frozen_at) * 1000

        labels = self.config["labels"]
        decoded = dict.fromkeys(labels, 0)
        tracked = dict.fromkeys(labels, 0)
        decoded_uncertain = 0
        tracked_uncertain = 0
        ambiguous = 0
        for obj in objects:
            if obj["label"] in decoded:
                decoded[obj["label"]] += 1
            else:
                decoded_uncertain += 1
        for row in rows:
            classification = row["classification"]
            if classification["accepted"] and classification["label"] in tracked:
                tracked[classification["label"]] += 1
            else:
                tracked_uncertain += 1
            ambiguous += bool(row["tracking"]["association_uncertain"])
        deltas = {
            label: view["class_counts"].get(label, 0) - previous_counts.get(label, 0)
            for label in labels
        }
        prior = previous_stages.get("cumulative", {})
        stages = dict(
            schema="native_count_stages_v1",
            source_frame_id=source,
            configuration_version=version,
            counting_session=self.count_session,
            model_sha256=self.model_sha256,
            labels=dict(zip(labels, self.config.get("titles", labels))),
            baseline_confirmed_counts=previous_stages.get(
                "baseline_confirmed_counts", dict(previous_counts)
            ),
            last_source=dict(
                decoded_by_class=decoded,
                decoded_uncertain=decoded_uncertain,
                tracked_by_class=tracked,
                tracked_uncertain=tracked_uncertain,
                decoded_objects=len(objects),
                tracked_rows=len(rows),
                ambiguous_rows=ambiguous,
                new_confirmed_counts=deltas,
            ),
            cumulative=dict(
                source_publications=prior.get("source_publications", 0) + 1,
                decoded_objects=prior.get("decoded_objects", 0) + len(objects),
                tracked_rows=prior.get("tracked_rows", 0) + len(rows),
                ambiguous_rows=prior.get("ambiguous_rows", 0) + ambiguous,
                decoded_uncertain=prior.get("decoded_uncertain", 0) + decoded_uncertain,
                tracked_uncertain=prior.get("tracked_uncertain", 0) + tracked_uncertain,
                decoded_by_class={
                    label: prior.get("decoded_by_class", {}).get(label, 0)
                    + decoded[label]
                    for label in labels
                },
                tracked_by_class={
                    label: prior.get("tracked_by_class", {}).get(label, 0)
                    + tracked[label]
                    for label in labels
                },
                new_confirmed_counts={
                    label: prior.get("new_confirmed_counts", {}).get(label, 0)
                    + deltas[label]
                    for label in labels
                },
            ),
            observation_totals_are_not_unique_counts=True,
            completely_undetected_parts_are_not_measured=True,
        )
        final_publish_wait_started = time.monotonic()
        with self.lock:
            final_wait = (time.monotonic() - final_publish_wait_started) * 1000
            if not self._valid(job):
                raise RuntimeError(
                    "Tracker ownership violated by an unordered state mutation"
                )
            self.effective_source = source
            self.effective_source_gaps += step - 1
            self.tracking_snapshot = view
            self.tracker_region_version = view["region_version"]
            self.count_stage_snapshot = stages
            self.rows = rows
            self.published_count += 1
            if failures:
                meta = dict(
                    source_frame_id=source,
                    rectangle=config["rectangle"],
                    version=version,
                    model_sha256=self.model_sha256,
                    recorded_monotonic=now,
                    token=job["token"],
                    boot_id=context.get("boot_id"),
                    counting_session=self.count_session,
                    run=self.selection.get("run"),
                    class_titles=dict(
                        zip(
                            self.config["labels"],
                            self.config.get("titles") or self.config["labels"],
                        )
                    ),
                )
                try:
                    self.queue.put_nowait((frame, failures, meta))
                except queue.Full:
                    try:
                        self.queue.get_nowait()
                        self.failure_dropped += 1
                    except queue.Empty:
                        pass
                    self.queue.put_nowait((frame, failures, meta))
            state_committed = time.monotonic()
            self.last_time = state_committed
            callback_completed = time.monotonic()
            sample.update(
                result_state_committed_monotonic=state_committed,
                received_to_state_commit_ms=(
                    state_committed - frame.capture_received_monotonic
                )
                * 1000,
                result_callback_completed_monotonic=callback_completed,
                final_result_publication_monotonic=callback_completed,
                final_publish_lock_wait_ms=final_wait,
                publication_wall_ms=(callback_completed - publish_entered) * 1000,
                result_ready_to_publish_ms=(callback_completed - sample["time"]) * 1000,
                received_to_publish_ms=(
                    callback_completed - frame.capture_received_monotonic
                )
                * 1000,
            )
            for row in rows:
                row.update(
                    result_state_committed_monotonic=state_committed,
                    received_to_state_commit_ms=sample["received_to_state_commit_ms"],
                    transport_dequeued_monotonic=getattr(
                        frame, "transport_dequeued_monotonic", 0.0
                    ),
                    python_copy_returned_monotonic=getattr(
                        frame, "python_copy_returned_monotonic", 0.0
                    ),
                    result_callback_completed_monotonic=callback_completed,
                    final_result_publication_monotonic=callback_completed,
                    received_to_publish_ms=sample["received_to_publish_ms"],
                )
            self.recent.append(callback_completed)
            self.ordered_latency.append(
                (callback_completed, sample["received_to_publish_ms"])
            )
            self.trace.append(
                dict(
                    sample,
                    configuration_version=version,
                    rectangle=config["rectangle"],
                    ordered_result_ms=sample["received_to_publish_ms"],
                    latency_scope="validated_DMA_receipt_to_final_publication_commit_including_both_state_lock_waits",
                )
            )
            if MINIMAL60:
                native_metrics.record_publication(self, sample)
            display_revision = self.display_revision
        if context.get("sensor_transition", False):
            self.action_output.invalidate()
        else:

            action_rows = []
            for row in rows:
                pose = row["classification_temporal"]
                current = row["classification"]

                confirmed = bool(
                    row["confirmed"]
                    and row["tracking"]["counting_eligible"]
                    and pose["accepted"]
                    and not pose["single_frame_label_disagrees"]
                    and (not current["accepted"] or pose["label"] == current["label"])
                )
                action_rows.append(
                    dict(
                        track_id=row["track_id"],
                        box=list(row["raw_box"]),
                        label=(
                            pose["label"]
                            if confirmed
                            else current["label"] if current["accepted"] else None
                        ),
                        confirmed=confirmed,
                        association_uncertain=bool(row["association_uncertain"]),
                    )
                )
            sent = self.action_output.publish(
                dict(
                    session=self.session + ":" + job["token"][2],
                    epoch=json.dumps(job["token"], separators=(",", ":")),
                    source_frame_id=int(source),
                    capture_monotonic=float(frame.capture_received_monotonic),
                    model_sha256=self.model_sha256,
                    rectangle=list(config["rectangle"]),
                    rows=action_rows,
                )
            )
            action_sent = time.monotonic()
            self.action_send_timing = (
                source,
                action_sent,
                (action_sent - frame.capture_received_monotonic) * 1000,
                sent,
            )
        if self.result_sink:
            self.result_sink.publish_results(
                display_revision,
                rows,
                self.config.get("window_model_id"),
                config["rectangle"],
                False,
                source,
            )

        self.sequence_capture.result(
            frame,
            objects,
            rows,
            sample,
            version,
            lambda: view["ledger"],
            diagnostics=rpc,
        )

    @staticmethod
    def _summary(values):
        return runtime_timing.summary(values)

    def _metrics(self, now):
        return native_metrics.snapshot(self, now)

    def snapshot(self):
        if self.error:
            raise RuntimeError("Rectangle pipeline failed") from self.error
        if self.status_writer.error:
            raise RuntimeError(
                "Async status storage failed: " + self.status_writer.error
            )
        now = time.monotonic()
        with self.lock:

            source_rows = self.rows
            last_time = self.last_time
            recent = tuple(self.recent)
            view = self.tracking_snapshot
            config = self.config
            settings = self.settings
            count_session = self.count_session
            history_scope = self.counter_history_scope
            model_sha256 = self.model_sha256
            engine_enabled = self.engine is not None
            display_revision = self.display_revision
            sensor_context = self.sensor_context
            count_stages = self.count_stage_snapshot
            version = self.version
            failure_dropped = self.failure_dropped
            acquisition_source = self.acquisition_source
            applied = self.applied_configuration
            metric_input = native_metrics.capture(self, now) if MINIMAL60 else None
            status_due = now >= self.next_status
            if status_due:
                self.next_status = now + 0.5

        rows = (
            [
                dict(
                    r,
                    cached_fine=dict(r["cached_fine"], age_ms=(now - last_time) * 1000),
                )
                for r in source_rows
            ]
            if now - last_time < 2.0
            else []
        )
        fine = sum(t > now - 2 for t in recent) / 2
        with self.capture_lock:
            roi_recent = tuple(self.roi_recent)
        roi = sum(t > now - 2 for t in roi_recent) / 2
        metrics = (
            native_metrics.summarize(self, metric_input)
            if MINIMAL60
            else self._metrics(now)
        )
        counts = view["class_counts"]
        ledger = view["ledger"]
        stats = dict(
            coarse_enabled=False,
            coarse_fps=0,
            fine_model_calls_per_second=sum(
                c["calls_per_second"] for c in metrics["cores"]
            ),
            effective_result_fps=fine,
            ordered_result_fps=fine,
            roi_supply_fps=roi,
            roi_calls_per_second=roi,
            native_roi=dict(
                origin=settings["rectangle"][:2], size=settings["rectangle"][2:]
            ),
            acquisition_source=acquisition_source,
            model_sha256=model_sha256,
            fine_state=(
                "manual_rectangle_fine_tracking"
                if engine_enabled
                else "manual_rectangle_capture"
            ),
            model_id=config.get("window_model_id") if config else None,
            counting=dict(
                mode="confirmed_unique_id",
                session_id=count_session,
                total=sum(counts.values()),
                current_present=len(rows),
                pending=sum(not r["confirmed"] for r in rows),
                unconfirmed=sum(
                    r["confirmed"]
                    and r["cached_fine"]["label"] in ("uncertain", "unreadable")
                    for r in rows
                ),
                class_counts=(
                    [
                        dict(label=label, title=title, count=counts.get(label, 0))
                        for label, title in zip(
                            config["labels"], config.get("titles", config["labels"])
                        )
                    ]
                    if config
                    else []
                ),
                **counts,
            ),
            display_revision=display_revision,
            sensor_context=sensor_context,
            configuration_version=version,
            settings=settings,
            failure_writer_dropped=failure_dropped,
            async_inference=metrics,
            ui_snapshot_compute_outside_pipeline_lock=True,
        )
        stats["counting"].update(ledger)
        stats["counting"]["total"] = ledger["observed_total"]
        stats["counting"]["history_scope"] = history_scope
        stats["tracking_owner"] = dict(
            owner="dedicated_tracking_process",
            publication_and_controls_owner="ordered_collector",
            heavy_tracking_outside_state_lock=True,
            readers_use_frozen_snapshot=True,
            commands="FIFO between complete source publications",
        )
        stats["tracking_process"] = self.tracker.diagnostics()
        if self.process_inference:
            stats["inference_execution_processes"] = [
                engine.diagnostics() for engine in self.engines
            ]
        stats["minimal_runtime"] = dict(
            enabled=MINIMAL60,
            tracking="serial_process",
            automatic_failure_images=not MINIMAL60,
            diagnostic_window=120 if MINIMAL60 else 2048,
        )
        stats["scheduling_locks"] = pipeline_control_io.snapshot(self)
        stats["background_evidence_process"] = dict(
            getattr(self, "evidence_process_status", {})
        )
        stats["terminal_logging"] = view.get("failure_event_cache", {})
        stats["count_stages"] = count_stages
        action_source, action_completed, action_latency, action_sent = (
            self.action_send_timing
        )
        stats["action_output_transport"] = dict(
            self.action_output.snapshot(),
            action_send_source_frame_id=action_source,
            action_send_completed_monotonic=action_completed,
            received_to_action_send_ms=action_latency,
            action_send_succeeded=action_sent,
            action_send_timing_scope="local socket sendto return; not actuator receipt or physical GPIO",
        )
        if status_due:

            self.status_writer.offer(
                self.folder / "status.json",
                dict(
                    protocol=WEB_CAPTURE_PROTOCOL,
                    version=version,
                    settings=settings,
                    pid=os.getpid(),
                    recorded_monotonic=now,
                    model_sha256=model_sha256,
                    inference_enabled=engine_enabled,
                    web_capture_protocol=WEB_CAPTURE_PROTOCOL,
                    web_capture_source=WEB_CAPTURE_SOURCE,
                    applied_configuration=applied,
                ),
            )
        return rows, stats

    def close(self):
        self.stop.set()
        self.action_output.close()
        if self.environment:
            self.environment.close()
        if self.thread.ident is not None:
            self.thread.join(10)
        if self.control_thread.ident is not None:
            self.control_thread.join(10)
        if self.scheduler:
            self.scheduler.close()
        self._tracking_started = False
        if getattr(self, "metrics_thread", None):
            self.metrics_thread.join(5)
            if self.metrics_thread.is_alive():
                raise RuntimeError("Metrics worker did not stop")
        if getattr(self, "metrics_process", None):
            self.metrics_shutdown_status = self.metrics_process.snapshot()
            if self.metrics_shutdown_status["error"]:
                raise RuntimeError(
                    "Metrics shutdown failed: " + self.metrics_shutdown_status["error"]
                )
        self.tracking_close_ack = self.tracker.close("runtime_stopped", timeout=5.0)
        self.refresh_tracking_snapshot()
        self.outcome_flush_complete = bool(self.tracking_close_ack.get("durable"))
        self.writer.join(5)
        if (
            self.thread.is_alive()
            or self.writer.is_alive()
            or self.control_thread.is_alive()
        ):
            raise RuntimeError("Rectangle workers did not stop")
        self.status_writer.close()
        for engine in self.engines:
            engine.release()
        if not self.outcome_flush_complete:
            raise RuntimeError(
                "Tracking outcome drain failed: "
                + str(self.tracking_close_ack.get("error"))
            )
