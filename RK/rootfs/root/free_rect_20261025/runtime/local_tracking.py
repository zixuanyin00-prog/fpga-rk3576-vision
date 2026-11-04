import copy
from contextlib import contextmanager
from types import SimpleNamespace
import os
import threading
import time

import cv2
import native_tracking
import object_outcomes
import passage_ledger
from tracking_state import Tracks


class LocalTracker:
    def __init__(
        self,
        initial_state=None,
        parent_context=None,
        *,
        cpu=None,
        configure=True,
        outcome_folder=None,
        timeout=8.0
    ):

        self._lock = threading.Lock()
        self._error = None
        self._closed = False
        self._final = None
        self._stats = {}
        self.sequence = 0
        self.context = dict(parent_context or {})
        self.owner_tid = None
        self.tracker = Tracks()
        cv2.setNumThreads(1)
        state = initial_state or {}
        tracker = self.tracker
        tracker.counts = dict(state.get("counts", {}))
        tracker.observed_total = state.get(
            "observed_total", sum(tracker.counts.values())
        )
        if "region_version" in state:
            tracker.region_version = state["region_version"]
        tracker.next_id = state.get("next_id", 0)
        if state.get("restore_record") is not None:
            passage_ledger.restore(tracker, state["restore_record"])
        store = object_outcomes.AsyncStore(
            folder=outcome_folder or object_outcomes.FOLDER, configure=configure
        )
        object_outcomes.configure(tracker, self.context, store=store)
        self._refresh_view()

    @contextmanager
    def _owned(self):
        if self._error:
            raise RuntimeError("Local tracking failed: " + self._error)
        if self._closed:
            raise RuntimeError("Local tracker is closed")
        if not self._lock.acquire(False):
            raise RuntimeError(
                "Concurrent local tracking violates the serial collector contract"
            )
        try:
            self.owner_tid = threading.get_native_id()
            self.sequence += 1
            yield
        except Exception as exc:
            self._error = str(exc)
            raise
        finally:
            self._lock.release()

    def _refresh_view(self):
        tracker = self.tracker

        self._view = copy.deepcopy(
            dict(
                class_counts=dict(tracker.counts),
                ledger=dict(
                    passage_ledger.summary(tracker),
                    pending_geometry_tracks=sum(
                        not track.get("observed_registered", False)
                        for track in tracker.tracks.values()
                    ),
                ),
                region_version=tracker.region_version,
                active_tracks=len(tracker.tracks),
                failure_event_cache=tracker.failure_event_store.snapshot(),
            )
        )
        return self._view

    def observe(
        self,
        frame,
        objects,
        source,
        now,
        hits,
        threshold,
        *,
        failure_event_context=None
    ):
        with self._owned():
            started = time.monotonic()
            cpu_started = time.thread_time()

            observation = SimpleNamespace(**vars(frame))
            observation.failure_event_context = copy.deepcopy(
                dict(failure_event_context or {})
            )
            rows, failures = native_tracking.observe_tracks(
                self.tracker, objects, source, now, hits, threshold, frame=observation
            )
            view = self._refresh_view()
            association = getattr(self.tracker, "association_stats", {})
            self._stats = dict(
                backend="direct_local_tracking",
                local_cpu_ms=(time.thread_time() - cpu_started) * 1000,
                local_wall_ms=(time.monotonic() - started) * 1000,
                local_pid=os.getpid(),
                owner_tid=self.owner_tid,
                child_cpu_ms=0.0,
                child_wall_ms=0.0,
                child_pid=None,
                child_cpus=None,
                rpc_wall_ms=0.0,
                observe_rpc_wall_ms=0.0,
                parent_rpc_cpu_ms=0.0,
                parent_frame_pack_cpu_ms=0.0,
                parent_frame_pack_wall_ms=0.0,
                request_bytes=0,
                response_bytes=0,
                shared_image_bytes=0,
                rgb_pipe_bytes=0,
                sequence=self.sequence,
                optical_flow_enabled=False,
                model_loaded=False,
                original_rgb_readonly=not frame.rgb.flags.writeable,
                tracking_phases=copy.deepcopy(
                    getattr(self.tracker, "conveyor_phase_timing", {})
                ),
                last_image_rejections=copy.deepcopy(
                    list(getattr(self.tracker, "last_image_rejections", ()))[:16]
                ),
                association={
                    name: association.get(name, 0)
                    for name in (
                        "active_tracks",
                        "new_tracks",
                        "registered_observed_tracks",
                        "counted_tracks",
                        "retired_after_coast",
                        "ambiguous_observations",
                        "image_rejected_observations",
                        "counted_reverse_pitch_links_rejected",
                        "new_tracks_with_predecessor_candidates",
                        "trusted_global_fast_frames",
                        "image_bootstrap_frames",
                        "image_bootstrap_whole_frame_frames",
                        "image_bootstrap_local_single_identity_frames",
                    )
                },
            )
            return rows, failures, view, dict(self._stats)

    def reset(self, kind="evidence"):
        if kind not in ("evidence", "region", "counts"):
            raise ValueError("Unknown tracking reset kind")
        with self._owned():
            if kind == "evidence":
                self.tracker.reset_evidence()
            elif kind == "region":
                self.tracker.reset()
            else:
                self.tracker.clear_counts()
            return self._refresh_view()

    def bind(self, context):
        with self._owned():
            self.context = dict(context)
            object_outcomes.configure(self.tracker, self.context)
            return self._refresh_view()

    def counters(self):
        return self._view["ledger"]

    def snapshot(self):
        return self._view

    @property
    def counts(self):
        return self._view["class_counts"]

    @property
    def region_version(self):
        return self._view["region_version"]

    @property
    def active_count(self):
        return self._view["active_tracks"]

    def diagnostics(self):
        return dict(
            backend="direct_local_tracking",
            pid=os.getpid(),
            child_process=False,
            error=self._error,
            closed=self._closed,
            last_rpc=dict(self._stats),
            original_pixels_transferred=False,
            original_pixels_referenced=True,
            model_loaded=False,
            auto_restart=False,
            owner_tid=self.owner_tid,
        )

    def close(self, reason="runtime_stopped", timeout=5.0):
        if self._closed:
            return self._final or dict(
                durable=False, error=self._error or "Local tracker already closed"
            )
        if not self._lock.acquire(False):
            return dict(
                durable=False, error="Local tracker is still owned by a publication"
            )
        started = time.monotonic()
        terminal_drained = False
        persistence = {}
        terminal_logs = {}
        try:
            try:
                if self._error is None:
                    object_outcomes.interrupt(self.tracker, reason)
            finally:

                try:
                    durable = self.tracker.outcome_store.close(
                        timeout=min(3.0, float(timeout))
                    )
                finally:
                    terminal_drained = self.tracker.failure_event_store.close(
                        timeout=max(0.1, float(timeout) - (time.monotonic() - started))
                    )
            persistence = self.tracker.outcome_store.snapshot()
            terminal_logs = self.tracker.failure_event_store.snapshot()
            self._refresh_view()
            self._final = dict(
                durable=bool(durable) and self._error is None,
                persistence=persistence,
                error=self._error or persistence.get("error"),
                terminal_logs_drained=terminal_drained,
                terminal_logs=terminal_logs,
            )
            return self._final
        except Exception as exc:
            self._error = self._error or str(exc)
            self._final = dict(
                durable=False,
                error=self._error,
                persistence=persistence,
                terminal_logs_drained=terminal_drained,
                terminal_logs=terminal_logs,
            )
            return self._final
        finally:
            self._closed = True
            self._lock.release()
