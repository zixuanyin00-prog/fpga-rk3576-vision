import uuid
import native_tracking
import object_outcomes


class Tracks:
    def __init__(self):
        self.tracks = {}
        self.region_version = uuid.uuid4().hex
        self.next_id = 0
        self.last_source = None
        self.counts = {}

    def reset(self):
        object_outcomes.interrupt(self, "configuration_changed")
        self.tracks.clear()
        self.region_version = uuid.uuid4().hex
        self.last_source = None

    def reset_evidence(self):
        object_outcomes.interrupt(self, "sensor_changed")
        self.tracks.clear()
        self.last_source = None

    def clear_counts(self):
        native_tracking.reset_counters(self)
        self.reset_evidence()
