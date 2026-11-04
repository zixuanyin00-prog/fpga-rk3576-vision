from pathlib import Path
from datetime import datetime
import heapq
import json
import os
import re
import stat


class EventRetention:
    def __init__(self, root, limit=100):
        self.events = Path(root).resolve() / "events"
        if self.events.resolve() != self.events:
            raise ValueError("Event archive directory is a reparse target")
        self.limit = limit
        self.records = {}
        self.order = []

    def initialize(self):
        self.records.clear()
        self.order.clear()
        for entry in os.scandir(self.events):
            if entry.is_dir(follow_symlinks=False) and re.fullmatch(
                "[0-9a-f]{64}", entry.name
            ):
                self.add(entry.name)
        return dict(deleted=0, freed_bytes=0, retained=len(self.records))

    def add(self, identity):
        if identity in self.records:
            return
        if not re.fullmatch("[0-9a-f]{64}", identity):
            raise ValueError("Invalid committed event ID")
        folder = self.events / identity
        if folder.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError("Event directory is outside archive or is a reparse point")
        receipt = folder / "receipt.json"
        if not receipt.exists():
            return
        value = json.loads(receipt.read_bytes())
        if value["event_id"] != identity:
            raise ValueError("Retention receipt identity differs")
        key = (
            datetime.fromisoformat(value["received_at"]).timestamp(),
            receipt.stat().st_mtime_ns,
            identity,
        )
        self.records[identity] = key
        heapq.heappush(self.order, key)

    def prune(self, protected=()):
        protected = set(protected)
        newest = {key[2] for key in heapq.nlargest(self.limit, self.records.values())}
        deferred = []
        deleted = freed = 0
        error = None
        while len(self.records) > self.limit and self.order and deleted < 128:
            key = self.order[0]
            identity = key[2]
            if identity in protected:
                deferred.append(heapq.heappop(self.order))
                continue
            if identity in newest:
                break
            folder = self.events / identity
            try:
                if (
                    folder.parent != self.events
                    or folder.lstat().st_file_attributes
                    & stat.FILE_ATTRIBUTE_REPARSE_POINT
                ):
                    raise ValueError("Refusing to remove an event outside the archive")
                files = list(folder.iterdir())
                for path in files:
                    info = path.lstat()
                    if (
                        path.name
                        not in (
                            "event.json",
                            "frame.rgb",
                            "manifest.json",
                            "receipt.json",
                        )
                        or not stat.S_ISREG(info.st_mode)
                        or info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
                    ):
                        raise ValueError(
                            "Unexpected file in committed event; retained: " + str(path)
                        )
                for path in sorted(files, key=lambda path: path.name == "receipt.json"):
                    size = path.stat().st_size
                    path.unlink()
                    freed += size
                folder.rmdir()
            except (OSError, ValueError) as exc:
                error = str(exc)
                break
            heapq.heappop(self.order)
            del self.records[identity]
            deleted += 1
        for key in deferred:
            heapq.heappush(self.order, key)
        return dict(
            deleted=deleted, freed_bytes=freed, retained=len(self.records), error=error
        )
