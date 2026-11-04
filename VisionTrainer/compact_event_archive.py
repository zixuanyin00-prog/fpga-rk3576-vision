from pathlib import Path
import ctypes
import heapq
import json
import os
import re
import stat
import sys
import time
from console_event_retention import EventRetention
from console_store import write

ROOT = Path(__file__).resolve().parent
ARCHIVE = ROOT / "device_event_logs"
REPORTS = ROOT / "device_event_previews"
PLAN = REPORTS / "retention_cleanup_plan.json"
STATUS = REPORTS / "retention_cleanup_status.json"


def main():
    events = (ARCHIVE / "events").resolve()
    if events != ROOT.resolve() / "device_event_logs" / "events":
        raise ValueError("Archive target differs from the explicit project directory")
    retention = EventRetention(ARCHIVE, limit=100)
    if "--execute" not in sys.argv:

        for entry in os.scandir(events):
            if not entry.is_dir(follow_symlinks=False) or not re.fullmatch(
                "[0-9a-f]{64}", entry.name
            ):
                continue
            info = entry.stat(follow_symlinks=False)
            if info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError("Refusing a reparse point in the event archive")
            retention.records[entry.name] = (
                info.st_mtime,
                info.st_mtime_ns,
                entry.name,
            )
        newest = sorted(retention.records.values(), reverse=True)[:100]

        cutoff = time.time() - 300
        protected = {
            identity for identity, key in retention.records.items() if key[0] >= cutoff
        }
        protected.update(key[2] for key in newest)
        plan = dict(
            events=str(events),
            records=list(retention.records.values()),
            protected=sorted(protected),
            count=len(retention.records),
            retention_limit=100,
            recent_receipts_protected_seconds=300,
            bulk_order="PC committed-directory mtime; final ordering by live collector receipt.received_at",
        )
        PLAN.write_text(json.dumps(plan), encoding="utf8")
        print(
            json.dumps(
                dict(
                    events=str(events),
                    count=plan["count"],
                    protected=len(protected),
                    old_events_to_remove=plan["count"] - len(protected),
                    plan=str(PLAN),
                )
            )
        )
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x4000)
    plan = json.loads(PLAN.read_text(encoding="utf8"))
    if plan["events"] != str(events):
        raise ValueError("Cleanup plan target changed")
    existing = {
        entry.name
        for entry in os.scandir(events)
        if entry.is_dir(follow_symlinks=False)
    }
    retention.order = [tuple(key) for key in plan["records"] if key[2] in existing]
    heapq.heapify(retention.order)
    retention.records = {key[2]: key for key in retention.order}
    deleted = plan["count"] - len(retention.records)
    freed = 0
    checkpoint = STATUS.with_suffix(".json.tmp")
    if not checkpoint.exists():
        checkpoint = STATUS
    if checkpoint.exists():
        previous = json.loads(checkpoint.read_text(encoding="utf8"))
        if previous["deleted"] + previous["planned_records_remaining"] == plan["count"]:
            freed = previous["freed_bytes"]
    while True:
        result = retention.prune(protected=plan["protected"])
        deleted += result["deleted"]
        freed += result["freed_bytes"]
        report = dict(
            state=(
                "cleaning"
                if result["deleted"]
                else "recent_receipts_left_for_live_collector"
            ),
            events=str(events),
            deleted=deleted,
            freed_bytes=freed,
            planned_records_remaining=result["retained"],
            error=result["error"],
            live_receiving_continued=True,
            retention_limit=100,
        )
        write(STATUS, report)
        if result["error"]:
            raise OSError(result["error"])
        if not result["deleted"]:
            break
    print(json.dumps(report))


if __name__ == "__main__":
    main()
