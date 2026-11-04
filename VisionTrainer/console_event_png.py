from pathlib import Path
from datetime import datetime, timezone
import csv
import ctypes
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from PIL import Image
from console_store import write

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "device_event_logs" / "events"
OUTPUT = ROOT / "device_event_previews"
LIMIT_BYTES = 1024**3
MIN_FREE_BYTES = 1024**3
KEEP_EVENTS = 100
COLUMNS = (
    "event_id",
    "object_id",
    "status",
    "recognition_result",
    "reason",
    "pc_received_at",
    "source_frame_id",
    "width",
    "height",
    "png",
    "source_folder",
    "rgb_sha256",
    "png_sha256",
    "error",
)


def start():
    OUTPUT.mkdir(exist_ok=True)
    with (OUTPUT / "worker.stderr.log").open("ab") as err:
        return subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--worker"],
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=err,
            creationflags=subprocess.CREATE_NO_WINDOW
            | subprocess.CREATE_NEW_PROCESS_GROUP,
        )


def run():

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateMutexW(None, False, r"Local\VisionTerminalPngNewOnly")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        kernel.CloseHandle(handle)
        return
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    priority = bool(kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x4000))
    try:
        OUTPUT.mkdir(exist_ok=True)
        pictures = OUTPUT / "png"
        pictures.mkdir(exist_ok=True)
        if pictures.resolve() != ROOT.resolve() / "device_event_previews" / "png":
            raise ValueError(
                "PNG directory differs from the explicit project directory"
            )
        baseline_path = OUTPUT / "baseline.json"
        if not baseline_path.exists():
            baseline_path.write_text(
                json.dumps(
                    dict(
                        enabled_at_utc=datetime.now(timezone.utc).isoformat(),
                        policy="new_events_only",
                        excluded_event_ids=sorted(
                            e.name for e in os.scandir(SOURCE) if e.is_dir()
                        ),
                    )
                ),
                encoding="utf8",
            )
        baseline = json.loads(baseline_path.read_text(encoding="utf8"))
        excluded = set(baseline["excluded_event_ids"])
        baseline["historical_logs_excluded"] = baseline.get(
            "historical_logs_excluded", len(excluded)
        )
        baseline["excluded_event_ids"] = sorted(
            e.name for e in os.scandir(SOURCE) if e.name in excluded
        )
        baseline_path.write_text(
            json.dumps(baseline, ensure_ascii=False), encoding="utf8"
        )
        excluded = set(baseline["excluded_event_ids"])
        records = {}
        index = OUTPUT / "index.csv"
        converted = skipped = errors = 0
        if index.exists():
            with index.open(encoding="utf-8-sig", newline="") as stream:
                for row in csv.DictReader(stream):
                    records[row["event_id"]] = row
                    errors += bool(row["error"])
                    converted += bool(row["png"])
                    skipped += not row["png"] and not row["error"]
        else:
            with index.open("w", encoding="utf-8-sig", newline="") as stream:
                csv.DictWriter(stream, COLUMNS).writeheader()
        used = sum(p.stat().st_size for p in pictures.iterdir() if p.is_file())
        last_error = None
        while True:
            state = "waiting_for_new_logs"
            count = 0
            candidates = []
            for entry in os.scandir(SOURCE):
                if not entry.is_dir() or not re.fullmatch("[0-9a-f]{64}", entry.name):
                    continue
                folder = Path(entry.path)
                try:
                    receipt_path = folder / "receipt.json"
                    receipt = json.loads(receipt_path.read_bytes())
                    key = (
                        datetime.fromisoformat(receipt["received_at"]).timestamp(),
                        receipt_path.stat().st_mtime_ns,
                    )
                except FileNotFoundError:
                    continue
                candidates.append((key, entry.name, folder))
            candidates = sorted(candidates, reverse=True)[:KEEP_EVENTS]
            active = {identity for key, identity, folder in candidates}
            records = {
                identity: row for identity, row in records.items() if identity in active
            }
            seen = excluded | records.keys()
            for path in pictures.glob("*.png"):
                if path.stem in active or not re.fullmatch("[0-9a-f]{64}", path.stem):
                    continue
                if (
                    path.resolve().parent != pictures.resolve()
                    or path.lstat().st_file_attributes
                    & stat.FILE_ATTRIBUTE_REPARSE_POINT
                ):
                    raise ValueError(
                        "Refusing to delete PNG outside the preview directory"
                    )
                try:
                    path.unlink()
                except PermissionError as exc:
                    last_error = str(exc)
            used = sum(p.stat().st_size for p in pictures.iterdir() if p.is_file())
            for key, identity, folder in candidates:
                if identity in seen:
                    continue
                row = dict.fromkeys(COLUMNS, "")
                row.update(event_id=identity, source_folder=str(folder))
                try:
                    event = json.loads((folder / "event.json").read_bytes())
                    receipt = json.loads((folder / "receipt.json").read_bytes())
                    if event["event_id"] != identity or receipt["event_id"] != identity:
                        raise ValueError("Committed log identity differs")
                    outcome = event["outcome"]
                    image = event["image"]
                    row.update(
                        object_id=event["object_id"],
                        status=outcome["status"],
                        recognition_result=outcome.get("recognition_result", ""),
                        reason=outcome["reason"],
                        pc_received_at=receipt["received_at"],
                    )
                    if image["available"]:
                        width, height = image["size"]
                        if (
                            image["format"] != "RGB888"
                            or type(width) is not int
                            or type(height) is not int
                            or min(width, height) <= 0
                            or width * height > 518400
                        ):
                            raise ValueError("Invalid native RGB geometry")
                        manifest = json.loads((folder / "manifest.json").read_bytes())
                        descriptor = next(
                            r for r in manifest["files"] if r["name"] == "frame.rgb"
                        )
                        raw = (folder / "frame.rgb").read_bytes()
                        rgb_sha = hashlib.sha256(raw).hexdigest()
                        if (
                            len(raw) != width * height * 3
                            or len(raw) != descriptor["bytes"]
                            or rgb_sha != descriptor["sha256"]
                        ):
                            raise ValueError(
                                "Original RGB size/hash differs from manifest"
                            )
                        destination = pictures / (identity + ".png")
                        if destination.exists():
                            encoded = destination.read_bytes()
                        else:
                            buffer = io.BytesIO()
                            Image.frombytes("RGB", (width, height), raw).save(
                                buffer, format="PNG", compress_level=6
                            )
                            encoded = buffer.getvalue()
                            if (
                                used + len(encoded) > LIMIT_BYTES
                                or shutil.disk_usage(OUTPUT).free - len(encoded)
                                < MIN_FREE_BYTES
                            ):
                                state = "preview_storage_paused"
                                break
                            temporary = destination.with_suffix(".tmp")
                            temporary.write_bytes(encoded)
                            os.replace(temporary, destination)
                            used += len(encoded)
                        row.update(
                            source_frame_id=image["source_frame_id"],
                            width=width,
                            height=height,
                            png=str(destination),
                            rgb_sha256=rgb_sha,
                            png_sha256=hashlib.sha256(encoded).hexdigest(),
                        )
                        converted += 1
                    else:
                        skipped += 1
                except FileNotFoundError:
                    continue
                except (OSError, ValueError, KeyError, StopIteration) as exc:
                    row["error"] = str(exc)
                    last_error = str(exc)
                    errors += 1
                records[identity] = row
                seen.add(identity)
                count += 1
                if count >= 32:
                    break
            temporary = index.with_suffix(".csv.tmp")
            with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, COLUMNS)
                writer.writeheader()
                writer.writerows(
                    sorted(
                        records.values(),
                        key=lambda row: row["pc_received_at"],
                        reverse=True,
                    )
                )
            try:
                os.replace(temporary, index)
            except PermissionError as exc:
                last_error = str(exc)
            status = dict(
                state=state,
                pid=os.getpid(),
                policy="new_events_only",
                enabled_at_utc=baseline["enabled_at_utc"],
                historical_logs_excluded=baseline["historical_logs_excluded"],
                converted=converted,
                no_image=skipped,
                errors=errors,
                last_error=last_error,
                retention_limit=KEEP_EVENTS,
                retained_index_rows=len(records),
                retained_pngs=sum(1 for p in pictures.glob("*.png")),
                png_bytes=used,
                png_quota_bytes=LIMIT_BYTES,
                original_archive_quota_changed=False,
                process_priority="below_normal" if priority else "unchanged",
                updated_at_utc=datetime.now(timezone.utc).isoformat(),
            )
            write(OUTPUT / "status.json", status)
            time.sleep(2 if count >= 32 else 5)
    finally:
        kernel.CloseHandle(handle)


if __name__ == "__main__":
    if "--worker" in sys.argv:
        run()
    else:
        print(start().pid)
