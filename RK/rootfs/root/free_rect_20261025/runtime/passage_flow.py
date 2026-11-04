from pathlib import Path
import ctypes as C
import os
import json
import time
import atexit
import numpy as np

_library = None
_load_error = None
_threads_configured = False
_parallel_error = None
_requested_threads = None


def _load():
    global _library, _load_error
    if _library is not None:
        return _library
    if _load_error is not None:
        return None
    try:
        import cv2

        lib = C.CDLL(str(Path(__file__).resolve().with_name("libpassage_flow.so")))
        lib.passage_flow_api_version.restype = C.c_int
        if lib.passage_flow_api_version() != 1:
            raise RuntimeError("LK cache API mismatch")
        lib.passage_flow_opencv_version.restype = C.c_char_p
        version = lib.passage_flow_opencv_version().decode()
        if (version, cv2.__version__) not in (("4.6.0", "4.11.0"),) and version.split(
            "."
        )[:2] != cv2.__version__.split(".")[:2]:
            raise RuntimeError(
                "LK cache OpenCV mismatch " + version + " vs " + cv2.__version__
            )
        lib.passage_flow_create.restype = C.c_void_p
        lib.passage_flow_destroy.argtypes = [C.c_void_p]
        lib.passage_flow_fb.argtypes = [
            C.c_void_p,
            C.c_void_p,
            C.c_void_p,
            C.c_int,
            C.c_int,
            C.c_size_t,
            C.c_size_t,
            C.c_void_p,
            C.c_int,
            C.c_void_p,
            C.c_void_p,
            C.c_void_p,
            C.c_void_p,
        ]
        lib.passage_flow_fb.restype = C.c_int
        _library = lib
    except Exception as exc:
        _load_error = str(exc)
    return _library


class Cache:
    def __init__(self, library):
        configure_native_threads()
        self.library = library
        self.handle = library.passage_flow_create()
        if not self.handle:
            raise MemoryError("Cannot create LK cache")

    def __del__(self):
        if getattr(self, "handle", None):
            self.library.passage_flow_destroy(self.handle)
            self.handle = None

    def run(self, previous, current, points):
        if not previous.flags.c_contiguous or not current.flags.c_contiguous:
            raise ValueError("LK cache needs contiguous grayscale")
        points = np.ascontiguousarray(points, dtype=np.float32)
        forward = np.empty_like(points)
        back = np.empty_like(points)
        status = np.empty((len(points), 1), np.uint8)
        back_status = np.empty_like(status)
        rc = self.library.passage_flow_fb(
            self.handle,
            previous.ctypes.data,
            current.ctypes.data,
            current.shape[1],
            current.shape[0],
            id(previous),
            id(current),
            points.ctypes.data,
            len(points),
            forward.ctypes.data,
            back.ctypes.data,
            status.ctypes.data,
            back_status.ctypes.data,
        )
        if rc:
            raise RuntimeError("LK cache execution error " + str(rc))
        self._retained_current = current
        return forward, status, back, back_status


def forward_backward(tracker, previous, current, points, options):
    library = _load()
    if library is not None:
        try:
            cache = getattr(tracker, "passage_flow_cache", None)
            if cache is None:
                tracker.passage_flow_cache = cache = Cache(library)
            result = cache.run(previous, current, points)
            tracker.passage_flow_backend = "native_exact_pyramid_cache"
            return result
        except Exception as exc:
            tracker.passage_flow_error = str(exc)
    else:
        tracker.passage_flow_error = _load_error
    import cv2

    tracker.passage_flow_backend = "opencv_reference"
    b, status, _ = cv2.calcOpticalFlowPyrLK(previous, current, points, None, **options)
    if b is None:
        return None, None, None, None
    back, back_status, _ = cv2.calcOpticalFlowPyrLK(
        current, previous, b, None, **options
    )
    return b, status, back, back_status


def select_groups(tracker, a, b, status, back, back_status, groups):
    library = _load()
    if library is None or not hasattr(library, "passage_flow_select_groups"):
        return None
    function = library.passage_flow_select_groups
    if not getattr(function, "_bound", False):
        function.argtypes = [C.c_void_p] * 5 + [
            C.c_int,
            C.c_void_p,
            C.c_int,
            C.c_void_p,
            C.c_void_p,
            C.c_void_p,
        ]
        function.restype = C.c_int
        function._bound = True
    boundaries = np.asarray(
        [(indices[0], indices[-1] + 1) for _, _, indices in groups], np.int32
    )
    shifts = np.zeros((len(groups), 2), np.float32)
    selected = np.zeros(len(a), np.uint8)
    support = np.zeros(len(groups), np.int32)
    result = function(
        a.ctypes.data,
        b.ctypes.data,
        back.ctypes.data,
        status.ctypes.data,
        back_status.ctypes.data,
        len(a),
        boundaries.ctypes.data,
        len(groups),
        shifts.ctypes.data,
        selected.ctypes.data,
        support.ctypes.data,
    )
    if result:
        tracker.passage_flow_group_error = "Native point filter error " + str(result)
        return None
    tracker.passage_flow_groups_backend = "native_float32_group_filter"
    return {
        key: (shifts[i], [index for index in indices if selected[index]])
        for i, (key, box, indices) in enumerate(groups)
        if support[i] >= 3
    }


_patch_statistics_backend = "reference_numpy"


def statistics_backend():
    return _patch_statistics_backend


def patch_descriptor(patch):
    global _patch_statistics_backend
    library = _load()
    if library is not None and hasattr(library, "passage_patch_stats"):
        function = library.passage_patch_stats
        if not getattr(function, "_bound", False):
            function.argtypes = [C.c_void_p, C.c_int, C.c_void_p, C.c_void_p]
            function.restype = C.c_int
            function._bound = True
        vector = np.empty_like(patch)
        stats = np.empty(3, np.float32)
        if (
            function(
                patch.ctypes.data, patch.size, vector.ctypes.data, stats.ctypes.data
            )
            == 0
        ):
            _patch_statistics_backend = "native_float32_patch_statistics"
            return dict(mean=float(stats[0]), std=float(stats[1]), vector=vector)
    center = patch - patch.mean()
    norm = float(np.linalg.norm(center))
    return dict(
        mean=float(patch.mean()),
        std=float(patch.std()),
        vector=center / max(norm, 1e-6),
    )


def face_edges(small):
    library = _load()
    if library is not None and hasattr(library, "passage_face_edges"):
        function = library.passage_face_edges
        if not getattr(function, "_bound", False):
            function.argtypes = [C.c_void_p, C.c_void_p]
            function.restype = C.c_int
            function._bound = True
        stats = np.empty(4, np.float32)
        if function(small.ctypes.data, stats.ctypes.data) == 0:
            return tuple(float(value) for value in stats)
    xp = np.abs(np.diff(small, axis=1))[5:23].mean(axis=0)
    yp = np.abs(np.diff(small, axis=0))[:, 5:35].mean(axis=1)
    return (
        float(yp.max()),
        float(xp.max()),
        float(xp[:10].mean()),
        float(xp[29:].mean()),
    )


def native_scheduling_snapshot():
    lib = _load()
    if lib is None or not hasattr(lib, "passage_flow_thread_snapshot"):
        return dict(configured=False, load_error=_load_error)
    lib.passage_flow_thread_snapshot.restype = C.c_char_p
    info = json.loads(lib.passage_flow_thread_snapshot().decode())
    info.update(
        pid=os.getpid(),
        requested_threads=_requested_threads,
        configuration_error=_parallel_error,
    )
    return info


def configure_native_threads():
    global _threads_configured, _parallel_error, _requested_threads
    if _threads_configured:
        return
    requested = os.environ.get("VISION_LK_NATIVE_THREADS", "1").strip()
    if requested not in ("1", "2"):
        raise ValueError("VISION_LK_NATIVE_THREADS must be 1 or 2")
    _requested_threads = int(requested)
    lib = _load()
    if lib is None or not hasattr(lib, "passage_flow_configure_threads"):
        if _requested_threads != 1:
            raise RuntimeError("Native parallel LK API unavailable")
        _threads_configured = True
        return
    lib.passage_flow_configure_threads.argtypes = [C.c_int]
    lib.passage_flow_configure_threads.restype = C.c_int
    rc = lib.passage_flow_configure_threads(_requested_threads)
    if rc:
        _parallel_error = (
            "Native thread plan rejected " + str(rc) + "; reverted to one native thread"
        )
        if lib.passage_flow_configure_threads(1):
            raise RuntimeError("Native LK serial fallback failed")
    _threads_configured = True
    artifact = os.environ.get("VISION_LK_NATIVE_PLAN_FILE")
    if artifact:
        target = Path(artifact)
        temporary = target.with_name(target.name + "." + str(os.getpid()) + ".tmp")
        temporary.write_text(
            json.dumps(
                dict(native_scheduling_snapshot(), recorded_monotonic=time.monotonic()),
                indent=2,
            )
        )
        temporary.replace(target)


def _shutdown_pool():
    try:
        if _library is not None and hasattr(_library, "passage_flow_shutdown_pool"):
            _library.passage_flow_shutdown_pool()
    except Exception:
        pass


atexit.register(_shutdown_pool)


_load()
