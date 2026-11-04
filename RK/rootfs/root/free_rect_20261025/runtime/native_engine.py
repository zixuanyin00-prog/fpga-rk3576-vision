from pathlib import Path
import ctypes as C
import os
import threading
import numpy as np


class _TensorInfo(C.Structure):
    _fields_ = [
        ("index", C.c_uint32),
        ("n_dims", C.c_uint32),
        ("dims", C.c_uint32 * 16),
        ("fmt", C.c_uint32),
        ("type", C.c_uint32),
        ("qnt_type", C.c_uint32),
        ("zp", C.c_int32),
        ("fl", C.c_int32),
        ("scale", C.c_float),
        ("n_elems", C.c_uint32),
        ("size", C.c_uint32),
        ("size_with_stride", C.c_uint32),
        ("w_stride", C.c_uint32),
        ("h_stride", C.c_uint32),
        ("name", C.c_char * 256),
    ]


class _Timing(C.Structure):
    _fields_ = [
        (name, C.c_double)
        for name in [
            "inputs_set_ms",
            "run_ms",
            "outputs_get_ms",
            "convert_ms",
            "release_ms",
            "total_ms",
        ]
    ]
    _fields_ += [("sequence", C.c_uint64)]


def _bind(path):
    lib = C.CDLL(str(path))
    definitions = {
        "native_engine_open": (
            [C.c_char_p, C.c_uint32, C.c_int, C.c_void_p, C.c_uint32],
            C.c_void_p,
        ),
        "native_engine_close": ([C.c_void_p], None),
        "native_engine_error": ([C.c_void_p], C.c_char_p),
        "native_engine_query": (
            [C.c_void_p, C.c_uint32, C.c_uint32, C.POINTER(_TensorInfo)],
            C.c_int,
        ),
        "native_engine_output_count": ([C.c_void_p], C.c_uint32),
        "native_engine_output": ([C.c_void_p, C.c_uint32], C.c_void_p),
        "native_engine_infer": ([C.c_void_p, C.c_void_p, C.c_uint32], C.c_int),
        "native_engine_infer_timed": (
            [C.c_void_p, C.c_void_p, C.c_uint32, C.POINTER(_Timing)],
            C.c_int,
        ),
        "native_engine_timing": ([C.c_void_p, C.POINTER(_Timing)], C.c_int),
        "native_engine_perf_run": ([C.c_void_p, C.POINTER(C.c_int64)], C.c_int),
        "native_engine_perf_detail": (
            [C.c_void_p, C.POINTER(C.c_void_p), C.POINTER(C.c_uint64)],
            C.c_int,
        ),
        "native_engine_version": (
            [C.c_void_p, C.c_void_p, C.c_void_p, C.c_uint32],
            C.c_int,
        ),
    }
    for name, (arguments, result) in definitions.items():
        fn = getattr(lib, name)
        fn.argtypes, fn.restype = arguments, result
    return lib


class NativeEngine:
    NPU_CORE_AUTO = 0
    NPU_CORE_0 = 1
    NPU_CORE_1 = 2
    NPU_CORE_0_1 = 3

    def __init__(
        self, verbose=False, library=None, reuse_outputs=False, collect_perf=False
    ):
        self.verbose = verbose
        self.library = Path(
            library
            or os.environ.get(
                "VISION_RKNN_C_LIBRARY", Path(__file__).with_name("libnative_engine.so")
            )
        )
        self.reuse_outputs = bool(reuse_outputs)
        self.collect_perf = bool(collect_perf)
        self._lib = None
        self._handle = None
        self._model = None
        self._views = []
        self.input_shape = None
        self._lock = threading.RLock()
        self.last_timing = {}

    def load_rknn(self, path):
        with self._lock:
            if self._handle:
                raise RuntimeError(
                    "Release the current engine before loading another model"
                )
            path = Path(path)
            if not path.is_file():
                raise FileNotFoundError(path)
            self._model = path.resolve()
            return 0

    def _check(self, result):
        if result:
            error = self._lib.native_engine_error(self._handle)
            raise RuntimeError(
                error.decode("utf-8", "replace") if error else "Native RKNN call failed"
            )

    def _require_open(self):
        if not self._handle:
            raise RuntimeError("RKNN engine is not initialized")

    def init_runtime(self, core_mask=0, **kwargs):
        if kwargs:
            raise TypeError("Unsupported runtime options: " + ", ".join(kwargs))
        with self._lock:
            if self._handle:
                raise RuntimeError("RKNN engine is already initialized")
            if self._model is None:
                raise RuntimeError("load_rknn must precede init_runtime")
            self._lib = _bind(self.library)
            error = C.create_string_buffer(512)
            self._handle = self._lib.native_engine_open(
                os.fsencode(self._model),
                int(core_mask),
                int(self.collect_perf),
                error,
                len(error),
            )
            if not self._handle:
                raise RuntimeError(error.value.decode("utf-8", "replace"))
            try:
                input_info = self._query(0, 0)
                shape = input_info["dims"]
                if input_info["fmt"] == 0:
                    self.input_shape = (shape[0], shape[2], shape[3], shape[1])
                elif input_info["fmt"] == 1:
                    self.input_shape = tuple(shape)
                else:
                    raise RuntimeError("Static RGB input layout is unsupported")
                if (
                    len(self.input_shape) != 4
                    or self.input_shape[0] != 1
                    or self.input_shape[-1] != 3
                ):
                    raise RuntimeError("Expected a static batch-1 RGB input")
                for index in range(self._lib.native_engine_output_count(self._handle)):
                    info = self._query(1, index)
                    shape = info["dims"]
                    if info["fmt"] == 1:
                        shape = [shape[0], shape[3], shape[1], shape[2]]
                    pointer = self._lib.native_engine_output(self._handle, index)
                    if not pointer:
                        raise RuntimeError("Native RKNN output buffer is missing")
                    flat = np.ctypeslib.as_array(
                        C.cast(pointer, C.POINTER(C.c_float)), shape=(info["n_elems"],)
                    )
                    self._views.append(flat.reshape(shape))
            except BaseException:
                self.release()
                raise
            return 0

    def inference(self, inputs, data_format=None, **kwargs):
        if kwargs:
            raise TypeError("Unsupported inference options: " + ", ".join(kwargs))
        with self._lock:
            self._require_open()
            if not isinstance(inputs, (list, tuple)) or len(inputs) != 1:
                raise ValueError("Exactly one RGB input is required")
            if data_format not in (None, ["nhwc"], ("nhwc",)):
                raise ValueError("Only explicit NHWC RGB input is supported")
            array = np.asarray(inputs[0])
            if array.dtype != np.uint8 or array.shape != self.input_shape:
                raise ValueError(
                    f"Expected uint8 NHWC {self.input_shape}; no resize or pixel conversion is performed"
                )
            array = np.ascontiguousarray(array)
            timing = _Timing()
            self._check(
                self._lib.native_engine_infer_timed(
                    self._handle, array.ctypes.data, array.nbytes, C.byref(timing)
                )
            )
            self.last_timing = {
                name: getattr(timing, name) for name, _ in timing._fields_
            }
            return (
                list(self._views)
                if self.reuse_outputs
                else [view.copy() for view in self._views]
            )

    def _query(self, which, index):
        info = _TensorInfo()
        self._check(
            self._lib.native_engine_query(self._handle, which, index, C.byref(info))
        )
        result = {
            name: getattr(info, name)
            for name, _ in info._fields_
            if name not in ("dims", "name")
        }
        result["dims"] = list(info.dims)[: info.n_dims]
        result["name"] = bytes(info.name).decode("utf-8", "replace")
        return result

    def tensor_attrs(self, native=False):
        with self._lock:
            self._require_open()
            return dict(
                inputs=[self._query(2 if native else 0, 0)],
                outputs=[
                    self._query(3 if native else 1, i)
                    for i in range(self._lib.native_engine_output_count(self._handle))
                ],
            )

    def query_perf_run(self):
        with self._lock:
            self._require_open()
            value = C.c_int64()
            self._check(self._lib.native_engine_perf_run(self._handle, C.byref(value)))
            return dict(run_duration_us=value.value)

    def query_perf_detail(self):
        with self._lock:
            self._require_open()
            pointer, length = C.c_void_p(), C.c_uint64()
            self._check(
                self._lib.native_engine_perf_detail(
                    self._handle, C.byref(pointer), C.byref(length)
                )
            )
            if length.value > 16 * 1024 * 1024:
                raise RuntimeError("Invalid RKNN performance report size")
            return (
                C.string_at(pointer, length.value).decode("utf-8", "replace")
                if length.value
                else ""
            )

    def sdk_version(self):
        with self._lock:
            self._require_open()
            api, driver = C.create_string_buffer(256), C.create_string_buffer(256)
            self._check(
                self._lib.native_engine_version(self._handle, api, driver, len(api))
            )
            return dict(api=api.value.decode(), driver=driver.value.decode())

    def release(self):
        with self._lock:
            self._views.clear()
            self.input_shape = None
            if self._handle:
                self._lib.native_engine_close(self._handle)
                self._handle = None

    def __del__(self):
        if getattr(self, "_handle", None):
            self.release()
