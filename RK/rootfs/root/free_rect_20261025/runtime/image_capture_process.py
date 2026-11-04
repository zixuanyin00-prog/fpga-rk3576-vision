from pathlib import Path
from types import SimpleNamespace
import os
import pickle
import sys
import numpy as np
import unrecognized_capture as capture


def main():
    test_folder = os.environ.get("VISION_EVIDENCE_PROCESS_TEST_FOLDER")
    if test_folder:
        capture.FOLDER = Path(test_folder)
    if hasattr(os, "sched_setaffinity"):
        os.sched_setaffinity(0, {3})
        os.setpriority(os.PRIO_PROCESS, 0, 10)
    for stream in (sys.stdin.buffer, sys.stdout.buffer):
        if stream is None:
            raise RuntimeError("Evidence subprocess requires private parent pipes")
    while True:
        try:
            payload = pickle.load(sys.stdin.buffer)
        except EOFError:
            return
        if payload is None:
            pickle.dump(dict(closed=True), sys.stdout.buffer, protocol=5)
            sys.stdout.buffer.flush()
            return
        root, source, x, y, shape, raw, rows, meta = payload
        try:
            rgb = np.frombuffer(raw, np.uint8).reshape(shape)
            frame = SimpleNamespace(source_frame_id=source, x=x, y=y, rgb=rgb)
            capture.save(Path(root), frame, rows, meta)
            response = dict(error=None, statistics=dict(capture._stats))
        except Exception as exc:
            capture.report_error(exc)
            response = dict(error=str(exc), statistics=dict(capture._stats))
        pickle.dump(response, sys.stdout.buffer, protocol=5)
        sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
