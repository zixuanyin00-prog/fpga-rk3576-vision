from dataclasses import asdict, replace
from functools import wraps
import copy
import math
import secrets
import threading
import time
import os
from sensor_device import _native, source_after
from environment_bridge import write

MODES = ("contrast", "histogram", "brightness", "digital_gain", "analog_gain")
DEFAULT = dict.fromkeys(MODES, True)
DEFAULT.update(frozen=True, analog_ceiling_db=18.0, digital_ceiling=1.5)


def validate_auto(v):
    if not isinstance(v, dict) or set(v) - set(DEFAULT):
        raise ValueError("Unknown automatic control")
    for k, x in v.items():
        if k in MODES or k == "frozen":
            if type(x) is not bool:
                raise ValueError("Automatic mode must be boolean")
        elif type(x) not in (int, float) or not math.isfinite(x):
            raise ValueError("Invalid ceiling")
        elif k == "analog_ceiling_db" and not 0 <= x <= 30:
            raise ValueError("Analog ceiling must be 0..30 dB")
        elif k == "digital_ceiling" and not 1 <= x <= 4:
            raise ValueError("Digital ceiling must be 1..4")
    return dict(v)


def enhance_submit(d, v):
    if len(v) != 12 or any(type(x) is not int for x in v):
        raise ValueError("Invalid enhancement bundle")
    if not 64 <= v[0] <= 1024 or not 0 <= v[1] <= 256 or not 0 < v[2] <= 0xFFFFFFFF:
        raise ValueError("Invalid gain, strength or revision")
    if any(not 0 <= x <= 256 for x in v[3:]) or v[3:] != sorted(v[3:]):
        raise ValueError("Invalid histogram knots")
    _native(d, "rgb_enhance_submit", v)


def enhance_status(d):
    v = _native(d, "rgb_enhance_status", [0] * 16, True)
    if v[:2] != [0x31545541, 1]:
        raise RuntimeError("Unsupported FPGA RGB enhancement ABI")
    processing = d.process_status()
    return dict(
        gain_q8=v[2],
        strength_q8=processing["requested"]["parameters"]["histogram_strength"],
        revision=v[4],
        knots=v[5:14],
        effective_revision=v[14],
        source_frame_id=v[15],
    )


def same_identity(a, b):

    return all(
        a.get(k) == b.get(k)
        for k in (
            "boot_id",
            "runtime_session_id",
            "firmware_sha256",
            "driver_sha256",
            "region_version",
            "origin",
        )
    )


def control_write(method):

    @wraps(method)
    def fenced(self, *args, **kwargs):
        transaction = getattr(self.c, "pipeline_control_io", None)
        if not callable(transaction):
            raise RuntimeError("Missing coordinator control-I/O transaction hook")
        with transaction():
            return method(self, *args, **kwargs)

    return fenced


class AutoISP:
    def __init__(self, console):
        self.c = console
        self.config = dict(DEFAULT)
        self.error = ""
        self.note = "等待传感器实读"
        self.metrics = None
        self.enhance = None
        self.sequence = secrets.randbelow(0xFFFFFFFE) + 1
        self.samples = []
        self.last_source = None
        self.last_adjust = 0.0
        self.settle_until = 0.0
        self.context = None
        self.adjustments = 0
        self.wait_timing = self._empty_wait_timing()
        self._sample_lock_started = None
        import transport

        transport.RGBTransport.enhance_submit = enhance_submit
        transport.RGBTransport.enhance_status = enhance_status
        self.enhance = self.call("enhance_status")
        try:
            import json

            saved = json.loads((console.folder / "saved_auto.json").read_text())
            self.config.update(validate_auto(saved))
        except FileNotFoundError:
            pass
        self.config["frozen"] = True
        self.thread = threading.Thread(
            target=self._loop, name="fpga-auto-isp-control", daemon=True
        )
        self.thread.start()

    def state(self):
        metrics = (
            dict(
                self.metrics,
                age_seconds=max(
                    0, time.monotonic() - self.metrics["recorded_monotonic"]
                ),
            )
            if self.metrics
            else None
        )
        return dict(
            config=dict(self.config),
            error=self.error,
            note=self.note,
            metrics=metrics,
            enhancement=self.enhance,
            adjustments=self.adjustments,
            exposure_mode="manual",
            statistic_stage="FPGA native ROI after base ISP and digital gain, before histogram mapping",
            accuracy_validated=False,
            control_waits=dict(self.wait_timing),
        )

    @staticmethod
    def _empty_wait_timing():
        return dict(
            version=1,
            read_hardware_calls=0,
            read_hardware_wait_ms_total=0.0,
            read_hardware_wait_ms_max=0.0,
            state_lock_acquisitions=0,
            state_lock_wait_ms_total=0.0,
            state_lock_wait_ms_max=0.0,
            state_lock_hold_ms_total=0.0,
            state_lock_hold_ms_max=0.0,
            stale_read_batches_discarded=0,
            missing_control_hooks=0,
            read_batches=0,
            readonly_waits_outside_pipeline_lock=True,
            real_control_writes_use_coordinator_transaction=True,
            last_read_method=None,
            last_lock_phase=None,
        )

    def _timing_add(self, name, value):
        timing = dict(self.wait_timing)
        timing[name + "_ms_total"] += value
        timing[name + "_ms_max"] = max(timing[name + "_ms_max"], value)
        self.wait_timing = timing

    def _acquire_state(self, phase):
        at = time.monotonic()
        locked = self.c.pipeline_state_lock.acquire(timeout=0.1)
        self._timing_add("state_lock_wait", (time.monotonic() - at) * 1000)
        if locked:
            self._sample_lock_started = time.monotonic()
            self.wait_timing = dict(
                self.wait_timing,
                state_lock_acquisitions=self.wait_timing["state_lock_acquisitions"] + 1,
                last_lock_phase=phase,
            )
        return locked

    def _release_state(self):
        at = self._sample_lock_started
        self._sample_lock_started = None
        self.c.pipeline_state_lock.release()
        self._timing_add("state_lock_hold", (time.monotonic() - at) * 1000)

    def _read_hardware(self, name, *args):
        if self._sample_lock_started is not None:
            raise RuntimeError(
                "Read-only hardware wait attempted inside pipeline state lock"
            )
        at = time.monotonic()
        try:
            return self.call(name, *args)
        finally:
            elapsed = (time.monotonic() - at) * 1000
            self._timing_add("read_hardware_wait", elapsed)
            self.wait_timing = dict(
                self.wait_timing,
                read_hardware_calls=self.wait_timing["read_hardware_calls"] + 1,
                last_read_method=name,
            )

    def _read_snapshot(self):

        epoch = self.c.control_epoch_provider()
        if type(epoch) is not int:
            raise RuntimeError("Invalid coordinator sensor epoch")
        return dict(
            epoch=epoch,
            identity=copy.deepcopy(self.c.environment.identity_provider()),
            sensor=copy.deepcopy(self.c.environment.state()),
            generation=self.c.panel.generation,
            panel_config=asdict(self.c.panel.config),
            panel_dirty=self.c.panel.dirty,
            automatic=dict(self.config),
            wb_auto=self.c.wb_auto,
            settle_until=self.settle_until,
        )

    def configure(self, values):
        self.config.update(validate_auto(values))
        self.error = ""
        self.samples = []
        self.last_source = None
        write(self.c.folder / "saved_auto.json", self.config)

    def call(self, name, *args):
        return self.c.camera._call(name, *args).result(timeout=2)

    @control_write
    def apply_enhancement(self, gain=None, strength=None, knots=None):
        current = self.call("enhance_status")
        self.sequence = (max(self.sequence, current["revision"]) + 1) & 0xFFFFFFFF or 1
        v = [
            current["gain_q8"] if gain is None else gain,
            current["strength_q8"] if strength is None else strength,
            self.sequence,
            *(current["knots"] if knots is None else knots),
        ]
        before = self.c.environment.state()
        self.c.environment.update(
            dict(before, sensor_transition=True, isp_transition=True)
        )
        self.call("enhance_submit", v)
        self.c.panel.generation += 1
        if self.c.on_isp_generation:
            self.c.on_isp_generation(self.c.panel.generation)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not self.c.stop.wait(0.025):
            s = self.call("enhance_status")
            if s["revision"] != self.sequence:
                raise RuntimeError("FPGA enhancement owner changed")
            if s["effective_revision"] == self.sequence:
                self.enhance = s
                self.finish_change(before, (s["source_frame_id"] + 3) & 0xFFFFFFFF)
                self.settle_until = time.monotonic() + 0.3
                return s
        raise TimeoutError("FPGA enhancement frame-boundary confirmation timed out")

    @control_write
    def base(self, **v):
        before = self.c.environment.state()
        self.c.environment.update(
            dict(before, sensor_transition=True, isp_transition=True)
        )
        self.c.panel.config = replace(self.c.panel.config, **v)
        self.c.panel.dirty = True
        self.c.panel.update(self.c.camera, self.c.controller)
        if self.c.panel.error:
            raise RuntimeError(self.c.panel.error)
        if self.c.on_isp_generation:
            self.c.on_isp_generation(self.c.panel.generation)
        self.call(
            "console_white_balance",
            self.c.wb_auto,
            self.c.panel.config.rg,
            self.c.panel.config.bg,
        )
        latest = self.c.camera.preview_source_id
        if latest is None:
            raise RuntimeError("No preview source fence")
        self.finish_change(before, (latest + 4) & 0xFFFFFFFF)
        self.c.save_isp()
        self.settle_until = time.monotonic() + 0.3

    def finish_change(self, before, fence):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not self.c.stop.wait(0.015):
            source = self.c.camera.preview_source_id
            if source is not None and source_after(source, fence):
                self.c.environment.update(
                    dict(
                        before,
                        sensor_transition=False,
                        isp_transition=False,
                        sensor_stable_source_id=fence,
                        enhancement_revision=(self.enhance or {}).get("revision"),
                    )
                )
                return
        raise TimeoutError("Waiting for fresh source frames after ISP change")

    @control_write
    def analog(self, code):
        env = self.c.environment
        before = env.session.readback(self.c.stop.is_set)
        env.update(dict(before, sensor_transition=True, sensor_state="submitted"))

        after = env.session.wait(
            env.session.request(before["shr0"], code), self.c.stop.is_set
        )
        env.update(after)
        if after["shr0"] != before["shr0"]:
            raise RuntimeError("Manual exposure shutter changed unexpectedly")
        self.settle_until = time.monotonic() + 0.4

    @staticmethod
    def histogram_knots(hist):
        total = sum(hist)
        cdf = [0]
        acc = 0
        for n in hist:
            acc += n
            cdf.append(round(acc * 256 / total))

        out = [0]
        for i in range(1, 8):
            out.append(
                max(
                    out[-1],
                    min(out[-1] + 64, max(i * 32 - 24, min(i * 32 + 24, cdf[i]))),
                )
            )
        out.append(256)
        return out

    def decide(self, q, s):
        n = q["pixels"]
        hist = q["histogram"]
        mean = q["luma_sum"] / n
        clip = q["clipped_pixels"] / n
        dark = q["dark_pixels"] / n
        self.metrics = dict(
            source_frame_id=q["source_frame_id"],
            mean_luma=mean,
            clipped_fraction=clip,
            dark_fraction=dark,
            histogram=list(hist),
            origin=q["origin"],
            producer="fpga",
            recorded_monotonic=time.monotonic(),
        )
        if self.config["frozen"] or not any(self.config[k] for k in MODES):
            return "五项自动调节已暂停；白平衡由独立开关控制"
        e = self.enhance
        cfg = self.config
        gain = s["gain_code"]
        digital = e["gain_q8"]
        p = self.c.panel.config
        low = mean < 102 and clip < 0.005
        high = mean > 124 or clip > 0.012
        analog_max = round(cfg["analog_ceiling_db"] / 0.3)
        digital_max = round(cfg["digital_ceiling"] * 256)
        if cfg["digital_gain"] and digital > digital_max:
            self.apply_enhancement(gain=max(digital_max, digital - 8))
            return "逐步收回超过上限的数字增益"
        if cfg["analog_gain"] and gain > analog_max:
            self.analog(max(analog_max, gain - 2))
            return "逐步收回超过上限的模拟增益"
        if high:
            if cfg["digital_gain"] and digital > 256:
                self.apply_enhancement(gain=max(256, digital - 8))
                return "降低数字增益，保护高光"
            if cfg["analog_gain"] and gain > 0:
                self.analog(max(0, gain - 2))
                return "降低模拟增益，保护高光"
        elif low:
            if cfg["analog_gain"] and gain < analog_max:
                self.analog(min(analog_max, gain + 2))
                return "曝光固定，缓慢提升模拟增益"
            if cfg["digital_gain"] and digital < digital_max:
                self.apply_enhancement(gain=min(digital_max, digital + 8))
                return "模拟增益已限幅，微调数字增益"
        if cfg["brightness"]:
            delta = (
                1
                if mean < 108 and clip < 0.005
                else -1 if mean > 118 or clip > 0.012 else 0
            )
            value = max(-16, min(16, p.brightness + delta))
            if delta and value != p.brightness:
                self.base(brightness=value)
                return "微调亮度偏移"
        cumulative = 0
        lo = 0
        hi = 255
        for i, count in enumerate(hist):
            cumulative += count
            if cumulative >= n * 0.1:
                lo = i * 32
                break
        cumulative = 0
        for i, count in enumerate(hist):
            cumulative += count
            if cumulative >= n * 0.9:
                hi = min(255, (i + 1) * 32)
                break
        if cfg["contrast"]:
            target = (
                256
                if clip > 0.005
                else max(224, min(320, round(180 / max(64, hi - lo) * 256)))
            )
            value = p.contrast + max(-4, min(4, target - p.contrast))
            if abs(target - p.contrast) > 8:
                self.base(contrast=value)
                return "按直方图动态范围微调对比度"
        if cfg["histogram"]:
            target = self.histogram_knots(hist)
            strength = 32 if clip > 0.005 else 64

            knots = [a + max(-2, min(2, b - a)) for a, b in zip(e["knots"], target)]
            if knots != e["knots"] or strength != e["strength_q8"]:
                self.apply_enhancement(strength=strength, knots=knots)
                return "更新保守的亮度直方图映射"
        if high:
            return "高光仍偏亮；可由你缩短曝光或调整光源"
        return (
            "光线不足，增益已达限幅；可由你调整曝光或光源"
            if low
            else "参数处于稳定区间"
        )

    def _loop(self):
        if hasattr(os, "sched_setaffinity"):
            os.sched_setaffinity(0, {0, 1, 2, 3})
        while not self.c.stop.wait(0.25):
            token = None
            locked = False
            try:
                if self.error:
                    continue
                if self.c.worker and self.c.worker.is_alive():
                    continue
                if any(
                    (self.c.folder / x).exists()
                    for x in ("request.json", "processing.json")
                ):
                    continue
                s = self.c.environment.state()
                if not s.get("sensor_register_readback") or s.get("sensor_transition"):
                    continue
                token = self.c.environment.acquire_lease("auto_isp", "regulate")
                if token is None:
                    continue
                io_lock = getattr(self.c.panel, "_vision_control_io_lock", None)
                if (
                    io_lock is None
                    or not hasattr(self.c, "pipeline_state_lock")
                    or not callable(getattr(self.c, "control_epoch_provider", None))
                    or not callable(getattr(self.c, "pipeline_control_io", None))
                ):
                    self.wait_timing = dict(
                        self.wait_timing,
                        missing_control_hooks=self.wait_timing["missing_control_hooks"]
                        + 1,
                    )
                    continue

                with io_lock:
                    locked = self._acquire_state("read_snapshot")
                    if not locked:
                        continue
                    now = time.monotonic()
                    if now < self.settle_until or now - self.last_adjust < 0.8:
                        continue
                    before = self._read_snapshot()
                    s = before["sensor"]
                    identity = before["identity"]
                    if not s.get("sensor_register_readback") or s.get(
                        "sensor_transition"
                    ):
                        continue
                    quality_origin = [
                        min(1408, identity["origin"][0]),
                        min(792, identity["origin"][1]),
                    ]
                    context = (
                        identity["region_version"],
                        s.get("sensor_generation"),
                        before["generation"],
                    )
                    change_origin = self.context != context
                    self._release_state()
                    locked = False
                    if change_origin:
                        self._read_hardware("quality_origin", quality_origin)
                    else:
                        q = self._read_hardware("quality_snapshot")
                        enhancement = self._read_hardware("enhance_status")
                    locked = self._acquire_state("read_commit")
                    if not locked:
                        self.wait_timing = dict(
                            self.wait_timing,
                            stale_read_batches_discarded=self.wait_timing[
                                "stale_read_batches_discarded"
                            ]
                            + 1,
                        )
                        continue
                    if before != self._read_snapshot():
                        self.wait_timing = dict(
                            self.wait_timing,
                            stale_read_batches_discarded=self.wait_timing[
                                "stale_read_batches_discarded"
                            ]
                            + 1,
                        )
                        continue
                    now = time.monotonic()
                    if change_origin:
                        self.context = context
                        self.samples = []
                        self.last_source = None
                        self.settle_until = now + 0.15
                        continue
                    self.enhance = enhancement
                    self.wait_timing = dict(
                        self.wait_timing,
                        read_batches=self.wait_timing["read_batches"] + 1,
                    )
                    latest = self.c.camera.preview_source_id
                    if (
                        q["fault"]
                        or q["pixels"] != 512 * 288
                        or sum(q["histogram"]) != q["pixels"]
                        or q["origin"] != quality_origin
                    ):
                        self.note = "等待有效的FPGA原生区域统计"
                        self.samples = []
                        continue
                    age = (
                        None
                        if latest is None
                        else (latest - q["source_frame_id"]) & 0xFFFFFFFF
                    )
                    if age is None or 30 < age < 0x80000000:
                        self.note = "统计帧已过期，暂停调节"
                        self.samples = []
                        continue
                    if (
                        age >= 0x80000000
                        and ((q["source_frame_id"] - latest) & 0xFFFFFFFF) > 3
                    ):
                        self.note = "等待预览与FPGA统计同步"
                        self.samples = []
                        continue
                    if not source_after(
                        q["source_frame_id"], s["sensor_stable_source_id"]
                    ):
                        continue
                    if enhancement["revision"] != enhancement["effective_revision"]:
                        continue
                    if q["source_frame_id"] == self.last_source:
                        continue
                    if self.last_source is not None and not source_after(
                        q["source_frame_id"], self.last_source
                    ):
                        self.samples = []
                        continue
                    self.last_source = q["source_frame_id"]
                    self.samples.append(q)
                    if len(self.samples) < 3:
                        continue
                    q = sorted(self.samples, key=lambda x: x["luma_sum"] / x["pixels"])[
                        1
                    ]
                    self.samples = []
                    self._release_state()
                    locked = False

                    self.note = self.decide(q, s)
                    self.last_adjust = now
                    self.adjustments += 1
            except Exception as exc:
                self.error = str(exc)
                self.note = "自动调节已暂停，保留当前参数；重新应用自动模式可重试"
            finally:
                if locked:
                    self._release_state()
                if token is not None:
                    self.c.environment.release_lease(token)
