import ctypes as C
import errno
import time
import numpy as np
from two_stage import Frame


class RGBTransport:
    def __init__(self, library, device="/dev/pcie_camera_vision", timeout=3.0):
        self.lib = C.CDLL(str(library), use_errno=True)
        for name, args in [
            ("jpeg_device_open", [C.c_char_p]),
            ("jpeg_device_start", [C.c_void_p, C.c_uint, C.c_uint]),
            (
                "jpeg_device_copy",
                [C.c_void_p, C.c_uint32, C.c_void_p, C.c_size_t, C.c_void_p],
            ),
            ("jpeg_device_status", [C.c_void_p, C.c_void_p]),
            ("jpeg_device_close", [C.c_void_p]),
            ("rgb_device_request", [C.c_void_p] + [C.c_uint] * 4),
        ]:
            fn = getattr(self.lib, name)
            fn.argtypes = args
            fn.restype = C.c_void_p if name == "jpeg_device_open" else C.c_int
        self.handle = self.lib.jpeg_device_open(device.encode())
        if not self.handle:
            raise OSError(C.get_errno(), "Cannot open three-stream RGB device")
        self.timeout = timeout
        self.last = 0
        self.buffer = C.create_string_buffer(1920 * 1080 * 3)
        self.meta = (C.c_uint32 * 24)()
        if self.lib.jpeg_device_start(self.handle, 0, 0):
            error = C.get_errno()
            self.close()
            raise OSError(error, "Cannot start RGB DMA")

    def capture(self, command):
        raise RuntimeError(
            "Use ContinuousTransport.acquire with the explicit ROI channel"
        )

    def close(self):
        if self.handle:
            handle = self.handle
            self.handle = None
            if self.lib.jpeg_device_close(handle):
                raise RuntimeError("DMA STOP fence failed; allocation quarantined")

    def feature_snapshot(self):
        fn = self.lib.rgb_device_features
        fn.argtypes = [C.c_void_p, C.c_void_p]
        fn.restype = C.c_int
        processing = self.process_status()
        if not processing["effective"]["effective_enabled"]["feature_statistics"]:
            return dict(
                enabled=False, valid=False, reason="feature_statistics disabled"
            )
        data = (C.c_uint32 * 82)()
        if fn(self.handle, data):
            if C.get_errno() == errno.EAGAIN:
                return None
            raise OSError(C.get_errno(), "Feature snapshot failed")
        return dict(
            enabled=True,
            valid=not bool(data[1]),
            processing_revision=processing["effective_revision"],
            feature_frame=data[0],
            fault=bool(data[1]),
            tile_values=list(data)[2:],
            fields=[
                "pixels",
                "luma_sum",
                "edge_pixels",
                "foreground_pixels",
                "saturated_pixels",
            ],
            field_meanings=[
                "tile pixel count",
                "post-histogram/pre-gray-fusion luma sum",
                "Sobel after cleanup/closing/foreground scope; excludes mask boundary",
                "luma >= feature_luma threshold; not foreground segmentation mask",
                "luma >= feature_highlight threshold",
            ],
            frame_relation="independent ISP feature sequence, not inference source frame",
        )

    def processing_status(self):
        fn = self.lib.rgb_device_processing_status
        fn.argtypes = [C.c_void_p, C.c_void_p]
        fn.restype = C.c_int
        values = (C.c_uint32 * 4)()
        if fn(self.handle, values):
            raise OSError(C.get_errno(), "Common processing identity read failed")
        return dict(
            fpga_epoch=values[0],
            processing_revision=values[1],
            industrial_config=values[2],
            feature_config=values[3],
        )

    def set_isp(self, config):
        from adaptive_isp import ISPConfig

        if not isinstance(config, ISPConfig):
            raise TypeError("Expected validated ISPConfig")

        class Wire(C.Structure):
            _fields_ = [
                ("rg", C.c_uint32),
                ("bg", C.c_uint32),
                ("contrast", C.c_uint32),
                ("brightness", C.c_int32),
                ("saturation", C.c_uint32),
                ("gaussian", C.c_uint32),
                ("he", C.c_uint32),
                ("revision", C.c_uint32),
            ]

        wb = self.lib.rgb_device_white_balance
        wb.argtypes = [C.c_void_p, C.c_uint, C.c_uint, C.c_uint]
        wb.restype = C.c_int
        if wb(self.handle, 1, config.rg, config.bg):
            raise OSError(C.get_errno(), "WB mailbox update failed")
        wire = Wire(
            config.rg,
            config.bg,
            config.contrast,
            config.brightness,
            config.saturation,
            config.gaussian,
            config.he,
            config.revision,
        )
        fn = self.lib.rgb_device_set_isp
        fn.argtypes = [C.c_void_p, C.POINTER(Wire)]
        fn.restype = C.c_int
        if fn(self.handle, C.byref(wire)):
            raise OSError(C.get_errno(), "FPGA ISP configuration failed")

    def industrial_snapshot(self):
        fn = self.lib.rgb_device_industrial
        fn.argtypes = [C.c_void_p, C.c_void_p]
        fn.restype = C.c_int
        data = (C.c_uint32 * 32)()
        if fn(self.handle, data):
            if C.get_errno() == errno.EAGAIN:
                return None
            raise OSError(C.get_errno(), "Industrial feature snapshot failed")
        return list(data)

    def industrial_config(self, value=None):
        if value is None:
            fn = self.lib.rgb_device_industrial_get_config
            fn.argtypes = [C.c_void_p, C.POINTER(C.c_uint)]
            fn.restype = C.c_int
            data = C.c_uint()
            if fn(self.handle, C.byref(data)):
                raise OSError(C.get_errno(), "Industrial config read failed")
            return data.value
        if (
            not isinstance(value, int)
            or value < 0
            or value & ~0x01FFFF1D
            or not value & 1
            or ((value >> 2) & 7) > 4
        ):
            raise ValueError("Invalid industrial config")
        fn = self.lib.rgb_device_industrial_config
        fn.argtypes = [C.c_void_p, C.c_uint]
        fn.restype = C.c_int
        if fn(self.handle, value):
            raise OSError(C.get_errno(), "Industrial config write failed")

    def process_status(self):
        from process_controls import status

        fn = self.lib.rgb_device_process_status
        fn.argtypes = [C.c_void_p, C.c_void_p]
        fn.restype = C.c_int
        values = (C.c_uint32 * 32)()

        for attempt in range(2):
            if not fn(self.handle, values):
                return status(values)
            error = C.get_errno()
            if error != errno.EAGAIN:
                break
        raise OSError(error, "PRC2 effective configuration read failed")

    def process_submit(self, words):
        fn = self.lib.rgb_device_process_submit
        fn.argtypes = [C.c_void_p, C.POINTER(C.c_uint32), C.POINTER(C.c_uint32)]
        fn.restype = C.c_int
        wire = (C.c_uint32 * 10)(*words)
        revision = C.c_uint32(0)
        if fn(self.handle, wire, C.byref(revision)):
            raise OSError(C.get_errno(), "PRC2 configuration submit failed")
        return revision.value
