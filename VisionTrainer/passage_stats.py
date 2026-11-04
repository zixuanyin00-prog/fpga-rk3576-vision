from collections import Counter
import math

MAX_WINDOW_SECONDS = 3600.0


class PassageWindow:
    def __init__(self, max_objects=100000):
        self.buckets = {}
        self.objects = {}
        self.sequence = 0
        self.available = False
        self.schema_version = 1
        self.started = None
        self.gap_at = None
        self.oldest_bucket = None
        self.max_objects = int(max_objects)
        self.discarded_through = -math.inf

    def ingest(self, stream, now):
        if self.started is None:
            self.started = float(now)
        if not stream or stream.get("version") not in (1, 2):
            return
        version = stream["version"]
        self.schema_version = max(self.schema_version, version)
        self.available = True
        for update in stream.get("updates", []):
            if not isinstance(update, (list, tuple)) or len(update) != (
                6 if version == 2 else 5
            ):
                continue
            seq, ident, entered, label, emitted = update[:5]
            state = (
                update[5]
                if version == 2
                else ("confirmed" if label is not None else "legacy_unresolved")
            )
            if state not in (
                "confirmed",
                "pending",
                "final_unconfirmed",
                "identity_lost",
                "interrupted",
                "legacy_unresolved",
            ):
                continue
            if type(seq) is not int or seq <= self.sequence:
                continue
            if seq != self.sequence + 1:
                self.gap_at = float(now)
            self.sequence = seq
            if (
                not isinstance(entered, (int, float))
                or not math.isfinite(entered)
                or entered > now
                or entered <= max(now - MAX_WINDOW_SECONDS, self.discarded_through)
            ):
                continue
            if label is not None and not isinstance(label, str):
                continue
            old = self.objects.get(ident)
            if old is not None:
                bucket = self.buckets[old[0]]
                item = bucket["objects"][ident]
                if item[0] != float(entered):
                    self.gap_at = float(now)
                    continue
                if (item[1], item[2]) != (label, state):
                    bucket["counts"][(item[1], item[2])] -= 1
                    bucket["counts"][(label, state)] += 1
                    item[1] = label
                    item[2] = state
            else:
                index = math.floor(float(entered) * 2)
                bucket = self.buckets.setdefault(
                    index, dict(objects={}, counts=Counter())
                )
                bucket["objects"][ident] = [float(entered), label, state]
                bucket["counts"][(label, state)] += 1
                self.objects[ident] = (index, float(entered))
                self.oldest_bucket = (
                    index
                    if self.oldest_bucket is None
                    else min(self.oldest_bucket, index)
                )
        if stream.get("sequence", self.sequence) > self.sequence:
            self.gap_at = float(now)
            self.sequence = int(stream["sequence"])
        while len(self.objects) > self.max_objects:
            index = min(self.buckets)
            bucket = self.buckets.pop(index)
            for ident, (entered, _, _) in bucket["objects"].items():
                self.objects.pop(ident, None)
                self.discarded_through = max(self.discarded_through, entered)
            self.gap_at = max(
                self.gap_at if self.gap_at is not None else -math.inf,
                self.discarded_through,
            )

    def prune(self, now):
        cutoff = math.floor((now - MAX_WINDOW_SECONDS) * 2) - 1
        if self.oldest_bucket is None:
            return
        for index in [index for index in self.buckets if index < cutoff]:
            bucket = self.buckets.pop(index)
            for ident in bucket["objects"]:
                self.objects.pop(ident, None)
        self.oldest_bucket = min(self.buckets, default=None)

    def summary(self, seconds, now, labels):
        seconds = float(seconds)
        self.prune(now)
        counts = Counter()
        start = float(now) - seconds
        for index in range(math.floor(start * 2), math.floor(float(now) * 2) + 1):
            bucket = self.buckets.get(index)
            if bucket is None:
                continue
            if index * 0.5 > start and (index + 1) * 0.5 <= now:
                counts.update(bucket["counts"])
            else:
                counts.update(
                    (label, state)
                    for entered, label, state in bucket["objects"].values()
                    if start < entered <= now
                )
        known = set(labels)
        classes = Counter()
        states = Counter()
        for (label, state), value in counts.items():
            if state == "confirmed" and label in known:
                classes[label] += value
            else:
                states[state if state != "confirmed" else "legacy_unresolved"] += value
        unknown = states["final_unconfirmed"] + states["legacy_unresolved"]
        total = sum(counts.values())
        complete = self.available and (self.gap_at is None or self.gap_at <= start)
        return dict(
            total=total,
            class_counts={label: classes[label] for label in labels},
            unrecognized=unknown,
            semantics_version=self.schema_version,
            final_unconfirmed=states["final_unconfirmed"],
            legacy_unresolved=states["legacy_unresolved"],
            pending=states["pending"],
            identity_lost=states["identity_lost"],
            observation_interrupted=states["interrupted"],
            rate=total / seconds if self.available else None,
            seconds=seconds,
            available=self.available,
            complete=complete,
            window_start=start,
            window_end=float(now),
            covered_seconds=min(
                seconds,
                max(0.0, now - (self.started if self.started is not None else now)),
            ),
        )
