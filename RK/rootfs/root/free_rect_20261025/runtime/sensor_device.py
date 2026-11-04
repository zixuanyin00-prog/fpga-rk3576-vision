import ctypes as C
import errno
import math
import time
import secrets

FIELDS = (
    "request_id",
    "error",
    "shr0",
    "gain_code",
    "vmax",
    "hmax",
    "mode",
    "read_mask",
    "frame_ticks_25mhz",
    "sensor_stable_source_id",
    "status_bits",
    "error_address",
    "previous_shr0",
    "previous_gain_code",
    "sensor_generation",
    "config_frame_sequence",
)


def _native(device, name, values, read=False):
    data = (C.c_uint32 * len(values))(*values)
    fn = getattr(device.lib, name)
    fn.argtypes = [C.c_void_p, C.c_void_p]
    fn.restype = C.c_int
    if fn(device.handle, data):
        raise OSError(C.get_errno(), name)
    return list(data)


def sensor_submit(device, request):
    values = [request[k] for k in ("request_id", "shr0", "gain_code", "flags")]
    if any(type(x) is not int for x in values) or not (
        0 < values[0] <= 0xFFFFFFFF
        and 8 <= values[1] <= 2279
        and 0 <= values[2] <= 100
        and 0 <= values[3] <= 2
    ):
        raise ValueError("Invalid bounded sensor request")
    _native(device, "rgb_sensor_submit", values)
    return {"submitted": True, "request_id": values[0]}


def sensor_status(device):
    values = _native(device, "rgb_sensor_status", [0] * 20, True)
    if values[:2] != [0x31584D49, 1]:
        raise RuntimeError("Unsupported sensor ABI")
    result = dict(zip(FIELDS, values[4:]))
    result.update(
        magic=values[0], abi=values[1], busy=bool(values[2]), submitted_id=values[3]
    )
    valid = (
        not result["busy"]
        and result["request_id"] == result["submitted_id"]
        and result["request_id"] != 0
        and result["error"] == 0
        and result["status_bits"] & 3 == 3
    )
    result.update(
        sensor_state="stable" if valid else "submitted" if result["busy"] else "error",
        sensor_transition=not valid,
        sensor_request_id=result["submitted_id"],
        sensor_register_readback=valid,
    )
    if valid:
        result["frame_period_us"] = result["frame_ticks_25mhz"] / 25
        result["line_period_us"] = result["frame_period_us"] / result["vmax"]
        result["exposure_us"] = (result["vmax"] - result["shr0"]) * result[
            "line_period_us"
        ] + 1.79
        result["analog_gain_db"] = result["gain_code"] * 0.3
        result["line_period_basis"] = (
            "derived from measured frame ticks and sensor-read VMAX; not independently measured XHS"
        )
    return result


def quality_origin(device, point):
    if (
        len(point) != 2
        or any(type(x) is not int for x in point)
        or not (0 <= point[0] <= 1408 and 0 <= point[1] <= 792)
    ):
        raise ValueError("Invalid quality region")
    fn = device.lib.rgb_quality_origin
    fn.argtypes = [C.c_void_p, C.c_uint]
    fn.restype = C.c_int
    if fn(device.handle, point[0] | point[1] << 16):
        raise OSError(C.get_errno(), "quality origin")


def quality_snapshot(device):
    v = _native(device, "rgb_quality_snapshot", [0] * 24, True)
    if v[0] != 0x31545351 or v[20] != 1:
        raise RuntimeError("Unsupported quality ABI")
    return dict(
        abi=1,
        sequence=v[1],
        source_frame_id=v[2],
        origin=[v[3] & 65535, v[3] >> 16],
        pixels=v[4],
        luma_sum=v[5],
        dark_pixels=v[6],
        clipped_pixels=v[7],
        pair_abs_difference_sum=v[8],
        pairs=v[9],
        fault=bool(v[10]),
        histogram=v[12:20],
        producer="fpga",
        proxy_only=True,
    )


def install_device_extension(cls):

    for name in (
        "sensor_submit",
        "sensor_status",
        "quality_origin",
        "quality_snapshot",
    ):
        if hasattr(cls, name) and getattr(cls, name) is not globals()[name]:
            raise RuntimeError("Sensor method already registered: " + name)
        setattr(cls, name, globals()[name])


def source_after(source, bound):
    return 0 <= ((source - bound) & 0xFFFFFFFF) < 0x80000000


class SensorSession:

    def __init__(self, camera):
        self.camera = camera
        self.last = None
        self.sequence = secrets.randbelow(0xFFFFFFFE) + 1

    def call(self, name, *args):
        return self.camera._call(name, *args).result(timeout=1)

    def status(self):
        return self.call("sensor_status")

    def request(self, shr0, gain_code, flags=0):
        self.sequence = (self.sequence + 1) & 0xFFFFFFFF or 1
        req = dict(
            request_id=self.sequence, shr0=shr0, gain_code=gain_code, flags=flags
        )
        self.call("sensor_submit", req)
        return req

    def wait(self, req, cancel=lambda: False, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cancel():
                raise RuntimeError(
                    "Cancelled; submitted hardware request may still finish; re-read before reuse"
                )
            try:
                s = self.status()
            except OSError as e:
                if e.errno != errno.EAGAIN:
                    raise
                time.sleep(0.02)
                continue
            if s["submitted_id"] != req["request_id"]:
                raise RuntimeError("Sensor owner/request changed")
            if not s["busy"] and s["request_id"] == req["request_id"]:
                self.last = s
                if s["error"] or s["sensor_transition"]:
                    raise RuntimeError("Sensor request failed: " + str(s))
                return s
            time.sleep(0.02)
        raise TimeoutError("Sensor completion deadline; capture remains inhibited")

    def readback(self, cancel=lambda: False):
        return self.wait(self.request(1735, 100, 1), cancel)

    def apply(self, exposure_us, gain_code, cancel=lambda: False, on_before=None):
        if not math.isfinite(exposure_us) or not 10 <= exposure_us <= 16000:
            raise ValueError("Exposure must be 10..16000 us")
        baseline = self.readback(cancel)
        if on_before is not None:
            on_before(baseline)
        shr = round(
            baseline["vmax"] - (exposure_us - 1.79) / baseline["line_period_us"]
        )
        if not 8 <= shr <= baseline["vmax"] - 4:
            raise ValueError("Exposure outside current frame timing")
        return baseline, self.wait(self.request(shr, gain_code), cancel)
