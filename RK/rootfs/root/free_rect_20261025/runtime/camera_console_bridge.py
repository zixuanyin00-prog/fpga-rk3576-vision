from pathlib import Path
from dataclasses import asdict, replace
import ctypes as C
import hashlib
import json
import math
import re
import threading
import time
import uuid
import os
import numpy as np
from adaptive_isp import ISPConfig
from process_controls import IDENTITY, apply_patch, effective_mask
from environment_bridge import write
from auto_isp import AutoISP, validate_auto, same_identity

LIMITS = {
    "rg": (128, 512),
    "bg": (128, 512),
    "contrast": (76, 768),
    "brightness": (-100, 100),
    "gaussian": (0, 256),
}
EXTRA = {"digital_gain": (64, 1024), "histogram": (0, 256)}


def validate_isp(values):
    if not isinstance(values, dict) or set(values) - set(LIMITS) - set(EXTRA) - {
        "wb_auto",
        "auto",
    }:
        raise ValueError("Unknown ISP parameter")
    for k, v in values.items():
        if k == "auto":
            validate_auto(v)
        elif k == "wb_auto":
            if type(v) is not bool:
                raise ValueError("wb_auto must be boolean")
        elif (
            type(v) is not int
            or not (LIMITS | EXTRA)[k][0] <= v <= (LIMITS | EXTRA)[k][1]
        ):
            raise ValueError("Invalid " + k)
    return values


def fpga_white_balance(device, automatic, r, b):
    fn = device.lib.rgb_device_white_balance
    fn.argtypes = [C.c_void_p, C.c_uint, C.c_uint, C.c_uint]
    fn.restype = C.c_int
    if fn(device.handle, 0 if automatic else 1, r, b):
        raise OSError(C.get_errno(), "FPGA white balance mailbox")


class CameraConsoleBridge:
    def __init__(
        self,
        camera,
        root,
        panel,
        controller,
        environment,
        native_provider,
        stop,
        *,
        inference_enabled=False,
        control_lock=None,
        on_isp_generation=None
    ):
        self.inference_enabled = inference_enabled
        self.control_lock = control_lock or threading.RLock()
        self.on_isp_generation = on_isp_generation
        self.camera = camera
        self.root = Path(root)
        self.panel = panel
        self.controller = controller
        self.environment = environment
        self.native_provider = native_provider
        self.stop = stop
        self.folder = self.root / "camera_console"
        self.folder.mkdir(exist_ok=True)
        self.images = Path("/dev/shm/camera_console_20261024")
        self.images.mkdir(exist_ok=True)
        self.wb_auto = False
        self.last_result = None
        self.next_poll = 0
        self.next_snapshot = 0
        self.worker = None
        self.serial = 0
        self.sensor_request_id = None
        self.sensor_observed = None
        setattr(
            __import__("transport").RGBTransport,
            "console_white_balance",
            fpga_white_balance,
        )
        try:
            saved = json.loads((self.folder / "saved_isp.json").read_text())
            validate_isp(saved)
            self.panel.config = replace(
                self.panel.config, **{k: v for k, v in saved.items() if k in LIMITS}
            )
            self.panel.dirty = True
            self.panel.update(camera, controller)
            self.wb_auto = saved.get("wb_auto", False)
        except FileNotFoundError:
            pass

        self.camera._call(
            "console_white_balance",
            self.wb_auto,
            self.panel.config.rg,
            self.panel.config.bg,
        ).result(timeout=2)
        self.auto = AutoISP(self)
        self.publisher = threading.Thread(
            target=self._publish, name="console-effect-snapshots", daemon=True
        )
        self.publisher.start()

    def status(self):

        with self.control_lock:
            return self._status_locked()

    def _status_locked(self):
        sensor = self.environment.state()
        if (
            sensor.get("sensor_register_readback")
            and sensor.get("request_id") != self.sensor_request_id
        ):
            self.sensor_request_id = sensor["request_id"]
            self.sensor_observed = time.monotonic()
        controls = self.camera._call("process_status").result(timeout=2)
        knots = (self.auto.enhance or {}).get("knots")
        controls["histogram_curve"] = dict(
            knots=knots,
            identity=knots == list(range(0, 257, 32)),
            source="existing frozen FPGA enhancement mailbox",
            toggle_recomputes_curve=False,
        )
        return dict(
            recorded_monotonic=time.monotonic(),
            identity=self.environment.identity_provider(),
            image_processing=IDENTITY,
            processing_status=self.camera._call("processing_status").result(timeout=2),
            processing_controls=controls,
            sensor=sensor,
            sensor_readback_observed_monotonic=self.sensor_observed,
            isp=dict(
                config=asdict(self.panel.config),
                wb_auto=self.wb_auto,
                generation=self.panel.generation,
                error=self.panel.error,
                confirmation="driver accepted FPGA mailbox; effective ISP source frame is not exported",
            ),
            automatic=self.auto.state(),
            capabilities=dict(
                sensor_hotupdate=True,
                wb_auto=True,
                limits=LIMITS | EXTRA,
                saturation=dict(supported=False, fixed=256),
                he=dict(supported=True),
                auto_brightness=True,
                auto_contrast=True,
                auto_analog_gain=True,
                auto_digital_gain=True,
                auto_histogram=True,
            ),
            last_result=self.last_result,
            busy=bool(self.worker and self.worker.is_alive()),
            image_root=str(self.images),
            snapshot_hz=1,
            inference_enabled=self.inference_enabled,
        )

    def _publish(self):
        try:
            if hasattr(os, "sched_setaffinity"):
                os.sched_setaffinity(0, {0, 1, 2, 3})
            while not self.stop.wait(0.25):
                write(self.folder / "status.json", self.status())
                if time.monotonic() < self.next_snapshot:
                    continue
                self.next_snapshot = time.monotonic() + 1
                self.serial += 1
                entries = {}
                for name, frame in [
                    ("overview", self.camera.latest),
                    ("native", self.native_provider()),
                ]:
                    if frame is None:
                        continue

                    pixels = np.ascontiguousarray(frame.rgb)
                    raw = memoryview(pixels).cast("B")
                    filename = name + "_" + str(self.serial % 4) + ".rgb"
                    target = self.images / filename
                    tmp = target.with_suffix(".tmp")
                    tmp.write_bytes(raw)
                    tmp.replace(target)
                    entries[name] = dict(
                        file=filename,
                        size=[frame.rgb.shape[1], frame.rgb.shape[0]],
                        source_frame_id=frame.source_frame_id,
                        origin=[frame.x, frame.y],
                        native_roi=bool(frame.roi),
                        sha256=hashlib.sha256(raw).hexdigest(),
                        recorded_monotonic=time.monotonic(),
                        sensor_control_generation_at_snapshot=self.environment.state().get(
                            "sensor_generation"
                        ),
                    )
                    entries[name][
                        "enhancement_control_state_at_snapshot"
                    ] = self.auto.enhance
                    entries[name].update(
                        fpga_epoch=frame.fpga_epoch,
                        processing_revision=frame.processing_revision,
                        enhance_revision=frame.enhance_revision,
                        per_image_enhancement_revision_exported=True,
                        processing_config=frame.processing_config,
                        image_processing=IDENTITY,
                        image_mode=(frame.processing_config or {}).get("image_mode"),
                        processing_metadata_valid=frame.processing_config is not None,
                    )
                    entries[name]["auto_frozen"] = self.auto.config["frozen"]
                write(
                    self.folder / "snapshots.json",
                    dict(
                        entries=entries,
                        identity=self.environment.identity_provider(),
                        recorded_monotonic=time.monotonic(),
                    ),
                )
        except Exception as e:
            write(self.folder / "publisher_error.json", dict(error=str(e)))

    def poll(self):
        if self.worker and self.worker.is_alive():
            return
        request = self.folder / "request.json"
        if not request.exists():
            return
        processing = self.folder / "processing.json"
        if processing.exists():
            return
        token = self.environment.acquire_lease("camera_console", "isp")
        if token is None:
            return
        request.rename(processing)
        self.worker = threading.Thread(
            target=self._apply,
            args=(processing, token),
            name="console-isp-control",
            daemon=True,
        )
        self.worker.start()

    def _apply(self, path, token):

        with self.control_lock:
            self._apply_locked(path, token)

    def _apply_locked(self, path, token):
        request = {}
        out = None
        try:
            request = json.loads(path.read_text())
            ident = request["id"]
            if not re.fullmatch(r"[A-Za-z0-9_]{1,80}", ident):
                raise ValueError("Invalid request ID")
            out = self.folder / ident
            out.mkdir(exist_ok=False)
            if not same_identity(
                request.get("identity", {}), self.environment.identity_provider()
            ):
                raise ValueError("Camera identity changed; refresh controls")
            if request.get("operation") == "readback":
                if request.get("values", {}):
                    raise ValueError("Readback does not accept parameters")
                after = self.environment.session.readback(self.stop.is_set)
                self.environment.update(after)
                self.last_result = dict(
                    id=ident,
                    ok=True,
                    sensor=after,
                    completed_monotonic=time.monotonic(),
                )
                return
            if request.get("operation") == "process":

                if not self.auto.config["frozen"]:
                    raise ValueError("请先暂停自动调节，再调整公共处理开关")
                current = self.camera._call("process_status").result(timeout=2)
                if current["pending"]:
                    raise ValueError("上一组公共处理参数尚未在帧边界生效")
                words = apply_patch(current["requested"]["words"], request["values"])
                write(out / "before.json", current)
                transaction = getattr(self, "pipeline_control_io", None)
                if not callable(transaction):
                    raise RuntimeError(
                        "Missing coordinator control-I/O transaction hook"
                    )
                with transaction():
                    before = self.environment.state()
                    self.environment.update(
                        dict(before, sensor_transition=True, isp_transition=True)
                    )
                    revision = self.camera._call("process_submit", words).result(
                        timeout=2
                    )
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline and not self.stop.wait(0.025):
                        state = self.camera._call("process_status").result(timeout=2)
                        if state["requested_revision"] != revision:
                            raise RuntimeError("PRC2 owner changed")
                        if state["fault"]:
                            raise RuntimeError("FPGA rejected processing configuration")
                        if (
                            state["effective_revision"] == revision
                            and state["source_valid"]
                        ):
                            expected = list(words)
                            expected[1] = effective_mask(words)
                            if state["effective"]["words"] != expected:
                                raise RuntimeError("PRC2 effective words differ")
                            self.auto.finish_change(
                                before,
                                (state["effective_source_frame_id"] + 3) & 0xFFFFFFFF,
                            )
                            self.panel.config = replace(
                                self.panel.config, gaussian=words[8]
                            )
                            self.save_isp()
                            write(
                                self.folder / "saved_process.json",
                                dict(
                                    interface=IDENTITY,
                                    words=words,
                                    revision=revision,
                                    configuration=state["effective"],
                                ),
                            )
                            self.last_result = dict(
                                id=ident,
                                ok=True,
                                processing_controls=state,
                                pixel_parameters_written=True,
                                completed_monotonic=time.monotonic(),
                            )
                            return
                    raise TimeoutError("PRC2 frame-boundary acknowledgement timed out")
            if request.get("operation") == "exposure":
                v = request["values"]
                if not isinstance(v, dict) or set(v) - {"exposure_us", "gain_code"}:
                    raise ValueError("Invalid exposure fields")
                if (
                    type(v.get("exposure_us")) not in (int, float)
                    or not math.isfinite(v["exposure_us"])
                    or not 10 <= v["exposure_us"] <= 16000
                ):
                    raise ValueError("Invalid exposure")
                if "gain_code" in v and (
                    type(v["gain_code"]) is not int or not 0 <= v["gain_code"] <= 100
                ):
                    raise ValueError("Invalid analog gain")
                transaction = getattr(self, "pipeline_control_io", None)
                if not callable(transaction):
                    raise RuntimeError(
                        "Missing coordinator control-I/O transaction hook"
                    )
                with transaction():
                    before = self.environment.session.readback(self.stop.is_set)
                    automatic_gain = (
                        self.auto.config["analog_gain"]
                        and not self.auto.config["frozen"]
                    )
                    code = (
                        before["gain_code"]
                        if automatic_gain
                        else v.get("gain_code", before["gain_code"])
                    )
                    self.environment.update(
                        dict(before, sensor_transition=True, sensor_state="submitted")
                    )
                    _, after = self.environment.session.apply(
                        v["exposure_us"], code, self.stop.is_set
                    )
                    self.environment.update(after)
                    self.auto.settle_until = time.monotonic() + 0.4
                    self.last_result = dict(
                        id=ident,
                        ok=True,
                        sensor=after,
                        completed_monotonic=time.monotonic(),
                    )
                    return
            values = validate_isp(request["isp"])
            before = self.status()
            write(out / "before.json", before)

            if set(values) == {"auto"}:
                self.auto.configure(values["auto"])
                self.last_result = dict(
                    id=ident,
                    ok=True,
                    isp=self.status()["isp"],
                    pixel_parameters_written=False,
                    completed_monotonic=time.monotonic(),
                )
                return
            transaction = getattr(self, "pipeline_control_io", None)
            if not callable(transaction):
                raise RuntimeError("Missing coordinator control-I/O transaction hook")
            with transaction():
                sensor_before = self.environment.state()
                self.environment.update(
                    dict(sensor_before, sensor_transition=True, isp_transition=True)
                )
                if "auto" in values:
                    self.auto.configure(values["auto"])
                modes = self.auto.config
                self.panel.config = replace(
                    self.panel.config,
                    **{
                        k: v
                        for k, v in values.items()
                        if k in LIMITS
                        and not (k in ("contrast", "brightness") and modes[k])
                    }
                )
                self.panel.auto = dict.fromkeys(self.panel.auto, False)
                self.panel.dirty = True
                self.panel.update(self.camera, self.controller)
                if self.on_isp_generation:
                    self.on_isp_generation(self.panel.generation)
                if self.panel.error:
                    raise RuntimeError(self.panel.error)
                self.wb_auto = values.get("wb_auto", self.wb_auto)
                self.camera._call(
                    "console_white_balance",
                    self.wb_auto,
                    self.panel.config.rg,
                    self.panel.config.bg,
                ).result(timeout=2)
                latest = self.camera.preview_source_id
                if latest is None:
                    raise RuntimeError("No preview source fence")
                self.auto.finish_change(sensor_before, (latest + 4) & 0xFFFFFFFF)
                gain = values.get("digital_gain") if not modes["digital_gain"] else None
                strength = values.get("histogram") if not modes["histogram"] else None
                if gain is not None or strength is not None:
                    knots = None
                    if strength:
                        q = self.auto.call("quality_snapshot")
                        if (
                            q["fault"]
                            or q["pixels"] != 512 * 288
                            or sum(q["histogram"]) != q["pixels"]
                        ):
                            raise RuntimeError("Invalid FPGA histogram")
                        knots = self.auto.histogram_knots(q["histogram"])
                    self.auto.apply_enhancement(
                        gain=gain, strength=strength, knots=knots
                    )
                self.save_isp()
                self.auto.settle_until = time.monotonic() + 0.4
                self.last_result = dict(
                    id=ident,
                    ok=True,
                    isp=self.status()["isp"],
                    completed_monotonic=time.monotonic(),
                )
        except Exception as e:
            self.auto.error = str(e)
            self.last_result = dict(
                id=request.get("id"),
                ok=False,
                error=str(e),
                partial_write_possible=True,
            )
        finally:
            if out:
                write(out / "result.json", self.last_result)
            self.environment.release_lease(token)
            path.unlink(missing_ok=True)

    def close(self):
        if self.worker:
            self.worker.join(5)
        self.auto.thread.join(5)
        self.publisher.join(3)

    def save_isp(self):
        saved = {k: getattr(self.panel.config, k) for k in LIMITS}
        saved["wb_auto"] = self.wb_auto
        write(self.folder / "saved_isp.json", saved)
