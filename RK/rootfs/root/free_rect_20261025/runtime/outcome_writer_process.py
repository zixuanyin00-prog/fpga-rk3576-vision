from pathlib import Path
import os
import pickle
import queue
import sys
import threading
import time
import outcome_disk_store


def main():
    folder = Path(sys.argv[1])
    configure = sys.argv[2] == "1"
    max_bytes = int(sys.argv[3])
    min_free_bytes = int(sys.argv[4])
    if max_bytes < 1 or min_free_bytes < 0:
        raise ValueError("Invalid storage quota")
    outcome_disk_store.MAX_BYTES = max_bytes
    outcome_disk_store.MIN_FREE_BYTES = min_free_bytes
    if configure and hasattr(os, "sched_setaffinity"):
        os.sched_setaffinity(0, {0, 1, 2})
        os.setpriority(os.PRIO_PROCESS, 0, 10)
    store = outcome_disk_store.DiskStore(folder, configure=configure)
    received = 0
    finished = threading.Event()

    def status(final=False, drained=False):
        snapshot = store.snapshot()
        processed = sum(
            snapshot.get(key, 0)
            for key in ("saved_events", "duplicate_events", "storage_limited_events")
        )
        snapshot.update(
            processed_events=processed,
            received_events=received,
            backend="dedicated_outcome_writer_process",
            writer_process_pid=os.getpid(),
            entry_script=str(Path(__file__).resolve()),
            all_accepted_events_durable=(
                (
                    drained
                    and processed == received
                    and not snapshot.get("storage_limited_events")
                    and not snapshot.get("error")
                )
                if final
                else None
            ),
        )
        pickle.dump(dict(final=final, status=snapshot), sys.stdout.buffer, protocol=5)
        sys.stdout.buffer.flush()

    def report():
        while not finished.wait(0.1):
            status()

    reporter = threading.Thread(
        target=report, name="outcome-process-status", daemon=True
    )
    reporter.start()
    try:
        while True:
            record = pickle.load(sys.stdin.buffer)
            if record is None:
                break
            received += 1
            while store.error is None:
                try:
                    store.work.put(record, timeout=0.1)
                    break
                except queue.Full:
                    continue
            if store.error:
                break
    except EOFError:
        store.error = (
            store.error or "Outcome publisher closed before the drain sentinel"
        )
    except Exception as exc:
        store.error = str(exc)
    finally:
        drained = store.close(timeout=30.0)
        finished.set()
        reporter.join()
        status(final=True, drained=drained)
    return 0 if drained and not store.error else 1


if __name__ == "__main__":
    raise SystemExit(main())
