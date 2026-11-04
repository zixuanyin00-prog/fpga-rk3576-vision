from pathlib import Path
import os
import pickle
import queue
import subprocess
import sys
import time


def write_failures(pipeline):
    os.sched_setaffinity(0, {3})
    os.setpriority(os.PRIO_PROCESS, 0, 10)
    script = Path(__file__).resolve().with_name("image_capture_process.py")
    error_log = (pipeline.root / "evidence_process_stderr.log").open("ab", buffering=0)
    process = subprocess.Popen(
        [sys.executable, "-u", str(script)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=error_log,
        bufsize=0,
    )
    pipeline.evidence_process_status = dict(
        backend="dedicated_image_evidence_process",
        pid=process.pid,
        alive=True,
        error=None,
        handled=0,
        original_raw_pixels=True,
        model_loaded=False,
    )
    try:
        while not pipeline.stop.is_set() or not pipeline.queue.empty():
            try:
                frame, failures, meta = pipeline.queue.get(timeout=0.2)
            except queue.Empty:
                continue
            with pipeline.lock:
                if meta.get("token") != pipeline._token():
                    continue

            at = time.monotonic()
            payload = (
                str(pipeline.root),
                int(frame.source_frame_id),
                int(frame.x),
                int(frame.y),
                frame.rgb.shape,
                frame.rgb.tobytes(),
                failures,
                meta,
            )
            pickle.dump(payload, process.stdin, protocol=5)
            process.stdin.flush()
            response = pickle.load(process.stdout)
            status = dict(
                pipeline.evidence_process_status,
                alive=process.poll() is None,
                handled=pipeline.evidence_process_status["handled"] + 1,
                last_worker_ms=(time.monotonic() - at) * 1000,
                statistics=response["statistics"],
                error=response["error"],
            )
            pipeline.evidence_process_status = status
            if response["error"]:
                pipeline.failure_dropped += 1
        pickle.dump(None, process.stdin, protocol=5)
        process.stdin.flush()
        if not pickle.load(process.stdout).get("closed"):
            raise RuntimeError("Evidence child did not confirm drain")
        process.stdin.close()
        process.wait(timeout=3)
        if process.returncode:
            raise RuntimeError("Evidence child failed: " + str(process.returncode))
    except Exception as exc:
        pipeline.failure_dropped += 1
        pipeline.evidence_process_status = dict(
            pipeline.evidence_process_status, error=str(exc), alive=False
        )

        if process.poll() is None:
            process.terminate()
        process.wait(timeout=3)
    finally:
        pipeline.evidence_process_status = dict(
            pipeline.evidence_process_status, alive=False
        )
        error_log.close()
