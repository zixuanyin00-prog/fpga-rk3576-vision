import ctypes as C
import pickle


class LatestResults:
    def __init__(self, context, capacity=1024 * 1024, slots=16):
        self.capacity = capacity
        self.slots = slots
        self.data = context.RawArray(C.c_ubyte, capacity * slots)
        self.locks = [context.Lock() for _ in range(slots)]
        self.revision = context.RawValue(C.c_ulonglong, 0)
        self.packet_revisions = context.RawArray(C.c_ulonglong, slots)
        self.sequences = context.RawArray(C.c_ulonglong, slots)
        self.sizes = context.RawArray(C.c_size_t, slots)
        self.sequence = context.RawValue(C.c_ulonglong, 0)
        self.published = context.RawValue(C.c_ulonglong, 0)
        self.busy_dropped = context.RawValue(C.c_ulonglong, 0)
        self.oversize_dropped = context.RawValue(C.c_ulonglong, 0)
        self.invalidated_dropped = context.RawValue(C.c_ulonglong, 0)

    def invalidate(self, revision):

        self.revision.value = revision

    def publish(self, revision, packet):
        if revision != self.revision.value:
            self.invalidated_dropped.value += 1
            return False
        raw = pickle.dumps(packet, protocol=5)
        if len(raw) > self.capacity:
            self.oversize_dropped.value += 1
            return False
        sequence = self.sequence.value + 1
        idx = sequence % self.slots
        if not self.locks[idx].acquire(False):
            self.busy_dropped.value += 1
            return False
        try:
            if revision != self.revision.value:
                self.invalidated_dropped.value += 1
                return False
            C.memmove(C.addressof(self.data) + idx * self.capacity, raw, len(raw))
            self.sizes[idx] = len(raw)
            self.packet_revisions[idx] = revision
            self.sequences[idx] = sequence
            self.sequence.value = sequence
            self.published.value += 1
            return True
        finally:
            self.locks[idx].release()

    def read_after(self, previous_sequence):
        newest = self.sequence.value
        revision = self.revision.value
        packets = []
        for sequence in range(
            max(previous_sequence + 1, newest - self.slots + 1), newest + 1
        ):
            idx = sequence % self.slots
            if not self.locks[idx].acquire(False):
                break
            try:
                if self.sequences[idx] != sequence:
                    break
                if self.packet_revisions[idx] != revision:
                    continue
                raw = C.string_at(
                    C.addressof(self.data) + idx * self.capacity, self.sizes[idx]
                )
            finally:
                self.locks[idx].release()

            packets.append((sequence, revision, pickle.loads(raw)))
        return packets if revision == self.revision.value else []

    def stats(self):
        idx = self.sequence.value % self.slots
        return dict(
            mode="collector_direct_source_history",
            capacity_bytes=self.capacity,
            history_slots=self.slots,
            allocated_bytes=self.capacity * self.slots,
            revision=self.revision.value,
            sequence=self.sequence.value,
            packet_bytes=self.sizes[idx],
            published=self.published.value,
            busy_dropped=self.busy_dropped.value,
            oversize_dropped=self.oversize_dropped.value,
            invalidated_dropped=self.invalidated_dropped.value,
            image_payload=False,
            blocking_on_display=False,
        )
