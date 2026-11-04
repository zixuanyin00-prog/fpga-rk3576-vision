from pathlib import Path
from types import SimpleNamespace
import ctypes as C
import hashlib
import json
import mmap
import os
import pickle
import subprocess
import sys
import threading
import time
import numpy as np

CAPACITY = 518400 * 3
MAX_MESSAGE = 2 * 1024 * 1024


class NativeInferenceProcess:
    NPU_CORE_AUTO = 0
    NPU_CORE_0 = 1
    NPU_CORE_1 = 2
    NPU_CORE_0_1 = 3

    def __init__(self, model_config, selection):
        if not selection or selection.get("confirmed") is not True:
            raise ValueError("Inference process requires the explicitly selected model")
        self.config = dict(model_config)
        self.selection = dict(selection)
        self.model_path = None
        self.core = None
        self.process = None
        self._fd = None
        self._mapping = None
        self._exchange = None
        self._response = C.c_void_p()
        self._response_capacity = C.c_size_t()
        self._response_size = C.c_size_t()
        self._sequence = 0
        self._lock = threading.Lock()
        self._closed = False
        self._error = None
        self._stats = {}
        self.stderr_tail = []
        self.stderr_thread = None
        self.input_shape = None

    def load_rknn(self, path):
        if self.process is not None or self._closed:
            raise RuntimeError("Inference process cannot change its selected model")
        selected = Path(self.selection["fine_model_path"]).resolve()
        if Path(path).resolve() != selected:
            raise ValueError("Inference path differs from the explicit model selection")
        self.model_path = str(selected)
        return 0

    def init_runtime(self, core_mask=0, **kwargs):
        if kwargs or core_mask not in (self.NPU_CORE_0, self.NPU_CORE_1):
            raise ValueError("Isolated inference requires one explicit NPU core")
        if self.model_path is None or self.process is not None or self._closed:
            raise RuntimeError(
                "Selected model must be set before process initialization"
            )
        import fcntl

        self.core = 0 if core_mask == self.NPU_CORE_0 else 1
        try:
            self._fd = os.memfd_create(
                "vision-npu-native-rgb-" + str(self.core), os.MFD_CLOEXEC
            )
            os.ftruncate(self._fd, CAPACITY)
            self._mapping = mmap.mmap(self._fd, CAPACITY, access=mmap.ACCESS_WRITE)
            self._exchange = C.CDLL(
                str(Path(__file__).with_name("libtracking_exchange.so"))
            )
            self._exchange.tracking_exchange.argtypes = [
                C.c_int,
                C.c_int,
                C.c_void_p,
                C.c_size_t,
                C.POINTER(C.c_void_p),
                C.POINTER(C.c_size_t),
                C.POINTER(C.c_size_t),
                C.c_double,
            ]
            self._exchange.tracking_exchange.restype = C.c_int
            self._exchange.tracking_exchange_free.argtypes = [C.c_void_p]
            self._exchange.tracking_exchange_free.restype = None
            self.process = subprocess.Popen(
                [sys.executable, "-u", str(Path(__file__).resolve()), "--worker"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                close_fds=True,
                bufsize=0,
                pass_fds=(self._fd,),
            )
            for pipe in (self.process.stdin, self.process.stdout):
                fcntl.fcntl(pipe.fileno(), fcntl.F_SETPIPE_SZ, 65536)
                os.set_blocking(pipe.fileno(), False)

            def drain_errors():
                for line in self.process.stderr:
                    self.stderr_tail.append(line.decode("utf-8", "replace").strip())
                    if len(self.stderr_tail) > 12:
                        del self.stderr_tail[:-12]

            self.stderr_thread = threading.Thread(
                target=drain_errors,
                name="native-inference-stderr-" + str(self.core),
                daemon=True,
            )
            self.stderr_thread.start()
            response = self._request(
                "init",
                dict(
                    core=self.core,
                    core_mask=core_mask,
                    model_path=self.model_path,
                    selection=self.selection,
                    config=self.config,
                    shared_fd=self._fd,
                    shared_capacity=CAPACITY,
                ),
                timeout=30.0,
            )
            self.input_shape = tuple(response["input_shape"])
        except BaseException:
            if self._error is None:
                self._error = "inference_process_initialization_failed"
            self.release()
            raise
        return 0

    def _request(self, operation, value=None, *, timeout=8.0):
        if self._closed or self.process is None:
            raise RuntimeError("Inference process is not open")
        if self._error is not None:
            raise RuntimeError("Inference process failed: " + self._error)
        self._sequence += 1
        sequence = self._sequence
        data = pickle.dumps(
            dict(sequence=sequence, operation=operation, value=value), protocol=5
        )
        if len(data) > MAX_MESSAGE:
            raise ValueError("Inference metadata exceeds the bounded message size")
        wire = len(data).to_bytes(8, "little") + data
        at = time.monotonic()
        try:
            rc = self._exchange.tracking_exchange(
                self.process.stdin.fileno(),
                self.process.stdout.fileno(),
                wire,
                len(wire),
                C.byref(self._response),
                C.byref(self._response_capacity),
                C.byref(self._response_size),
                at + timeout,
            )
            if rc:
                raise OSError(-rc, os.strerror(-rc))
            if self._response_size.value > MAX_MESSAGE:
                raise ValueError("Inference reply exceeds metadata size limit")
            response = pickle.loads(
                C.string_at(self._response, self._response_size.value)
            )
            if response["sequence"] != sequence:
                raise RuntimeError("Inference reply sequence mismatch")
            if response.get("error"):
                raise RuntimeError(response["error"])
            self._stats = dict(
                response.get("stats", {}),
                exchange_wall_ms=(time.monotonic() - at) * 1000,
                request_bytes=len(data),
                response_bytes=self._response_size.value,
                sequence=sequence,
            )
            return response
        except Exception as exc:
            self._error = type(exc).__name__ + ": " + str(exc)
            if self.process.poll() is None:
                self.process.kill()
            self.process.wait(timeout=5.0)
            raise RuntimeError("Selected NPU process failed: " + self._error) from exc

    def execute(self, core, job):
        if core != self.core:
            raise ValueError("Inference core does not match its process")
        frame = job["frame"]
        rgb = frame.rgb
        if (
            rgb.dtype != np.uint8
            or rgb.ndim != 3
            or rgb.shape[2] != 3
            or not 0 < rgb.shape[0] * rgb.shape[1] <= 518400
        ):
            raise ValueError("Inference process requires bounded original RGB pixels")
        if not self._lock.acquire(False):
            raise RuntimeError(
                "Concurrent use would overwrite the shared inference image"
            )
        try:
            started = time.monotonic()
            copy_cpu = time.thread_time()
            target = np.ndarray(rgb.shape, np.uint8, buffer=self._mapping)
            np.copyto(target, rgb, casting="no")
            del target
            copy_cpu_ms = (time.thread_time() - copy_cpu) * 1000
            copy_wall_ms = (time.monotonic() - started) * 1000
            frame_info = dict(
                shape=rgb.shape,
                x=int(frame.x),
                y=int(frame.y),
                source_frame_id=int(frame.source_frame_id),
                capture_received_monotonic=frame.capture_received_monotonic,
                transport_dequeued_monotonic=getattr(
                    frame, "transport_dequeued_monotonic", 0.0
                ),
                python_copy_returned_monotonic=getattr(
                    frame, "python_copy_returned_monotonic", 0.0
                ),
            )
            value = dict(
                core=core,
                source=int(frame.source_frame_id),
                token=job["token"],
                frame=frame_info,
                settings=job["config"],
                times={
                    name: job.get(name)
                    for name in (
                        "acquired_monotonic",
                        "scheduler_admitted_monotonic",
                        "npu_worker_started_monotonic",
                    )
                },
            )
            response = self._request("execute", value)
            if (
                response["source"] != frame.source_frame_id
                or response["core"] != core
                or response["token"] != job["token"]
            ):
                self._error = "inference_source_core_or_epoch_mismatch"
                self.process.kill()
                self.process.wait(timeout=5.0)
                raise RuntimeError(self._error)
            self._stats = dict(
                self._stats,
                shared_copy_wall_ms=copy_wall_ms,
                shared_copy_cpu_ms=copy_cpu_ms,
                shared_image_bytes=rgb.nbytes,
                parent_execution_wall_ms=(time.monotonic() - started) * 1000,
            )
            return {
                name: response[name]
                for name in ("objects", "sample", "core_samples", "model_calls")
            }
        finally:
            self._lock.release()

    def diagnostics(self):
        return dict(
            backend="exec_npu_and_native_decode",
            pid=self.process.pid if self.process else None,
            core=self.core,
            closed=self._closed,
            error=self._error,
            model_sha256=self.selection["fine_sha256"],
            native_rgb_shared=True,
            shared_capacity_bytes=CAPACITY,
            input_slots=1,
            rgb_pipe_bytes=0,
            raw_heads_transferred=False,
            last_rpc=dict(self._stats),
            stderr_tail=list(self.stderr_tail),
        )

    def release(self):
        if self._closed:
            return
        try:
            if (
                self.process is not None
                and self.process.poll() is None
                and self._error is None
            ):
                self._request("close", timeout=3.0)
        finally:
            self._closed = True
            if self.process is not None:

                try:
                    self.process.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=3.0)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=5.0)
                if self.stderr_thread is not None:
                    self.stderr_thread.join(2.0)
                for pipe in (
                    self.process.stdin,
                    self.process.stdout,
                    self.process.stderr,
                ):
                    if not pipe.closed:
                        pipe.close()
            if self._exchange is not None:
                self._exchange.tracking_exchange_free(self._response)
                self._response = C.c_void_p()
            if self._mapping is not None:
                self._mapping.close()
                self._mapping = None
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None


def _read_exact(stream, size):
    parts = []
    while size:
        part = stream.read(size)
        if not part:
            raise EOFError("Inference request pipe closed mid-message")
        parts.append(part)
        size -= len(part)
    return b"".join(parts)


def worker_main(source=None, destination=None):
    source = sys.stdin.buffer if source is None else source
    owns_destination = destination is None
    if owns_destination:

        sys.stdout.flush()
        destination = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
        os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    mapping = None
    engine = None
    pipeline = None
    shared_fd = None
    core = None
    try:
        while True:
            prefix = source.read(8)
            if not prefix:
                return
            prefix += _read_exact(source, 8 - len(prefix))
            size = int.from_bytes(prefix, "little")
            if size > MAX_MESSAGE:
                raise ValueError("Inference request exceeds metadata size limit")
            request = pickle.loads(_read_exact(source, size))
            operation = request["operation"]
            value = request["value"]
            response = dict(sequence=request["sequence"])
            started = time.monotonic()
            cpu_started = time.thread_time()
            try:
                if operation == "init":
                    if pipeline is not None:
                        raise RuntimeError("NPU process already initialized")
                    core = value["core"]
                    if core not in (0, 1) or value["core_mask"] != (1, 2)[core]:
                        raise ValueError("Invalid isolated NPU core assignment")
                    if value["shared_capacity"] != CAPACITY:
                        raise ValueError("Inference shared image capacity mismatch")
                    selection = value["selection"]
                    if selection.get("confirmed") is not True:
                        raise ValueError(
                            "Selected model has not been explicitly confirmed"
                        )
                    model_path = Path(selection["fine_model_path"]).resolve()
                    if Path(value["model_path"]).resolve() != model_path:
                        raise ValueError("Worker path differs from selected model")
                    if (
                        hashlib.sha256(model_path.read_bytes()).hexdigest()
                        != selection["fine_sha256"]
                    ):
                        raise ValueError("Selected NPU model hash mismatch")
                    config_raw = Path(selection["fine_config_path"]).read_bytes()
                    if (
                        hashlib.sha256(config_raw).hexdigest()
                        != selection["config_sha256"]
                    ):
                        raise ValueError("Selected NPU configuration hash mismatch")
                    config = json.loads(config_raw)
                    if config != value["config"]:
                        raise ValueError(
                            "Parent and selected model configuration differ"
                        )
                    shared_fd = value["shared_fd"]
                    if os.fstat(shared_fd).st_size != CAPACITY:
                        raise ValueError("Shared inference descriptor size mismatch")
                    mapping = mmap.mmap(shared_fd, CAPACITY, access=mmap.ACCESS_READ)
                    os.sched_setaffinity(0, {6})
                    os.sched_setscheduler(0, os.SCHED_OTHER, os.sched_param(0))
                    os.setpriority(os.PRIO_PROCESS, 0, -5)
                    sys.setswitchinterval(0.0005)
                    import cv2

                    cv2.setNumThreads(1)
                    import passage_pixels
                    from native_engine import NativeEngine
                    from full_roi_runtime import validate_model_config

                    validate_model_config(config)
                    engine = NativeEngine(verbose=False, reuse_outputs=True)
                    engine.load_rknn(str(model_path))
                    engine.init_runtime(core_mask=value["core_mask"])

                    os.sched_setaffinity(0, {6})
                    os.sched_setscheduler(0, os.SCHED_OTHER, os.sched_param(0))
                    os.setpriority(os.PRIO_PROCESS, 0, -5)
                    pipeline = SimpleNamespace(
                        config=config,
                        engines=[engine, engine],
                        _valid=lambda job: True,
                        stale_results=0,
                        core_samples=[[], []],
                        core_calls=[0, 0],
                        sample_lock=threading.Lock(),
                    )
                    response["input_shape"] = engine.input_shape
                elif operation == "execute":
                    if pipeline is None or value["core"] != core:
                        raise RuntimeError(
                            "Inference execute before matching core initialization"
                        )
                    info = dict(value["frame"])
                    height, width, channels = info.pop("shape")
                    if (
                        channels != 3
                        or height <= 0
                        or width <= 0
                        or height * width > 518400
                    ):
                        raise ValueError("Invalid shared inference RGB shape")
                    if info["source_frame_id"] != value["source"]:
                        raise ValueError("Inference source descriptor mismatch")
                    rgb = np.frombuffer(
                        mapping, np.uint8, count=height * width * 3
                    ).reshape(height, width, 3)
                    frame = SimpleNamespace(rgb=rgb, **info)
                    job = dict(
                        frame=frame,
                        config=value["settings"],
                        token=value["token"],
                        **value["times"]
                    )
                    pipeline.core_samples[core] = []
                    before_calls = pipeline.core_calls[core]
                    from full_roi_runtime import infer

                    try:
                        objects, sample = infer(pipeline, core, job)

                        features_started = time.monotonic()
                        features_cpu_started = time.thread_time()
                        gray = cv2.cvtColor(frame.rgb, cv2.COLOR_RGB2GRAY)
                        boxes = [obj["bbox"] for obj in objects]
                        origin = (frame.x, frame.y)
                        support = passage_pixels.supports(gray, boxes, origin)
                        appearance = passage_pixels.descriptors(
                            gray, boxes, origin, inset=0.12
                        )
                        for index, obj in enumerate(objects):
                            descriptor = appearance[index]
                            obj["_real_pixel_features"] = dict(
                                support=support[index],
                                appearance=(
                                    None
                                    if descriptor is None
                                    else dict(
                                        mean=descriptor["mean"],
                                        std=descriptor["std"],
                                        vector=descriptor["vector"].tobytes(order="C"),
                                    )
                                ),
                            )
                        features_finished = time.monotonic()
                        feature_timing = dict(
                            pixel_features_cpu_ms=(
                                time.thread_time() - features_cpu_started
                            )
                            * 1000,
                            pixel_features_wall_ms=(
                                features_finished - features_started
                            )
                            * 1000,
                            time=features_finished,
                            received_to_result_ms=(
                                features_finished - frame.capture_received_monotonic
                            )
                            * 1000,
                        )

                        sample.update(feature_timing)
                        for row in pipeline.core_samples[core]:
                            if row is not sample:
                                row.update(feature_timing)
                    finally:
                        del frame, rgb, job
                    response.update(
                        source=value["source"],
                        core=core,
                        token=value["token"],
                        objects=objects,
                        sample=sample,
                        core_samples=pipeline.core_samples[core],
                        model_calls=pipeline.core_calls[core] - before_calls,
                    )
                elif operation == "close":
                    response["closed"] = True
                else:
                    raise ValueError("Unknown inference process operation")
                response["stats"] = dict(
                    child_pid=os.getpid(),
                    core=core,
                    child_cpus=sorted(os.sched_getaffinity(0)),
                    nice=os.getpriority(os.PRIO_PROCESS, 0),
                    policy=os.sched_getscheduler(0),
                    child_cpu_ms=(time.thread_time() - cpu_started) * 1000,
                    child_wall_ms=(time.monotonic() - started) * 1000,
                    native_rgb_readonly=True,
                    raw_heads_transferred=False,
                )
                if operation == "execute":
                    response["stats"].update(
                        pixel_features_cpu_ms=sample["pixel_features_cpu_ms"],
                        pixel_features_wall_ms=sample["pixel_features_wall_ms"],
                    )
            except Exception as exc:
                response["error"] = type(exc).__name__ + ": " + str(exc)
            data = pickle.dumps(response, protocol=5)
            if len(data) > MAX_MESSAGE:
                raise ValueError("Decoded inference metadata exceeds reply limit")
            remaining = memoryview(len(data).to_bytes(8, "little") + data)
            while remaining:
                written = destination.write(remaining)
                if not written:
                    raise EOFError("Inference response pipe closed")
                remaining = remaining[written:]
            destination.flush()
            if operation == "close" or response.get("error"):
                return
    finally:
        if engine is not None:
            engine.release()
        if mapping is not None:
            mapping.close()
        if shared_fd is not None:
            os.close(shared_fd)
        if owns_destination:
            destination.close()


if __name__ == "__main__":
    if sys.argv[1:] != ["--worker"]:
        raise SystemExit(
            "native_inference_process is started by the selected-model runtime"
        )
    worker_main()
