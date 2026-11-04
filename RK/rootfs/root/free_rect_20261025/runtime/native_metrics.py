import os
import threading
import time
from operator import itemgetter
from metrics_process_bridge import MetricsProcess

FIELDS = (
    "queue_wait_ms",
    "native_ring_wait_ms",
    "admitted_to_worker_ms",
    "preprocessing_ms",
    "input_copy_ms",
    "decode_ms",
    "rknn_ms",
    "received_to_result_ms",
)
PUBLISH_FIELDS = (
    "tracking_wall_ms",
    "tracking_cpu_ms",
    "tracking_parent_cpu_ms",
    "tracking_total_cpu_ms",
    "tracking_child_wall_ms",
    "tracking_rpc_wall_ms",
    "tracking_frame_pack_cpu_ms",
    "publish_lock_wait_ms",
    "final_publish_lock_wait_ms",
    "tracking_snapshot_ms",
    "publication_wall_ms",
    "result_ready_to_publish_ms",
    "received_to_state_commit_ms",
    "received_to_publish_ms",
)
SCHEDULER_FIELDS = (
    "pending_wait_ms",
    "worker_execution_ms",
    "completed_waiting_ms",
    "publication_callback_ms",
    "occupied_residence_ms",
)
CORE_WIRE_FIELDS = FIELDS + ("time",)
TRACE_WIRE_FIELDS = PUBLISH_FIELDS + (
    "source_frame_id",
    "result_callback_completed_monotonic",
    "final_result_publication_monotonic",
)
ENGINE_FIELDS = (
    "inputs_set_ms",
    "run_ms",
    "outputs_get_ms",
    "convert_ms",
    "release_ms",
    "total_ms",
    "sequence",
)


def pack_request(value):
    get_core = itemgetter(*CORE_WIRE_FIELDS)
    get_trace = itemgetter(*TRACE_WIRE_FIELDS)
    get_engine = itemgetter(*ENGINE_FIELDS)
    get_scheduler = itemgetter(*SCHEDULER_FIELDS)
    since = value["now"] - 30
    return dict(
        wire_version=1,
        now=value["now"],
        calls=value["calls"],
        model_sha=value["model_sha"],
        rows=[
            [
                (
                    get_core(row),
                    (
                        get_engine(row["engine_timing"])
                        if "engine_timing" in row
                        else None
                    ),
                )
                for row in rows
                if row["time"] > since
            ]
            for rows in value["rows"]
        ],
        trace=[
            get_trace(row)
            for row in value["trace"]
            if row.get("result_callback_completed_monotonic", 0) > since
        ],
        recent_trace=value["trace"][-12:],
        capture=[row for row in value["capture"] if row[0] > since],
        ordered=[row for row in value["ordered"] if row[0] > since],
        scheduler=list(map(get_scheduler, value["scheduler"])),
        snapshot_cpu_ms=value.get("snapshot_cpu_ms", 0.0),
        snapshot_wall_ms=value.get("snapshot_wall_ms", 0.0),
    )


def empty(now):
    def summary():
        return dict(samples=0, mean=None, p50=None, p95=None, max=None)

    return dict(
        counters_monotonic=now,
        cores=[
            dict(
                core=c,
                calls=0,
                calls_per_second=0,
                **{field: summary() for field in FIELDS}
            )
            for c in (0, 1)
        ],
        capture_wait_ms=summary(),
        received_to_ordered_result_ms=summary(),
        publication={field: summary() for field in PUBLISH_FIELDS},
        recent_ordered_results=[],
        scheduler_phase_timing={field: summary() for field in SCHEDULER_FIELDS},
    )


def start(pipeline):
    if getattr(pipeline, "metrics_thread", None):
        return
    parent_cpu = os.environ.get("VISION_METRICS_PARENT_CPU", "0,1,2")
    if parent_cpu not in ("0,1,2", "7"):
        raise ValueError("Statistics parent CPU must be 7 or default 0,1,2")
    parent_cpus = (7,) if parent_cpu == "7" else (0, 1, 2)
    pipeline.metric_cache = empty(time.monotonic())

    def commit(cache):
        with pipeline.lock:
            pipeline.metric_cache = cache

    bridge = MetricsProcess(commit, parent_cpus=parent_cpus)
    pipeline.metrics_process = bridge

    def worker():
        try:
            bridge.register_thread("snapshot")
            due = time.monotonic()
            while not pipeline.stop.is_set():
                if bridge.available():
                    started = time.monotonic()
                    cpu_started = time.thread_time()
                    with pipeline.sample_lock:
                        rows = [list(s) for s in pipeline.core_samples]
                        calls = list(pipeline.core_calls)
                    with pipeline.capture_lock:
                        capture = list(pipeline.capture_wait)
                    with pipeline.lock:
                        now = time.monotonic()
                        ordered = list(pipeline.ordered_latency)
                        trace = list(pipeline.trace)
                        model_sha = pipeline.model_sha256
                    scheduler = (
                        pipeline.scheduler.timing_snapshot(now - 30)
                        if pipeline.scheduler
                        else []
                    )
                    value = dict(
                        now=now,
                        rows=rows,
                        calls=calls,
                        capture=capture,
                        ordered=ordered,
                        trace=trace,
                        model_sha=model_sha,
                        scheduler=scheduler,
                        snapshot_cpu_ms=(time.thread_time() - cpu_started) * 1000,
                        snapshot_wall_ms=(time.monotonic() - started) * 1000,
                    )
                    value = pack_request(value)
                    value.update(
                        snapshot_cpu_ms=(time.thread_time() - cpu_started) * 1000,
                        snapshot_wall_ms=(time.monotonic() - started) * 1000,
                    )
                    bridge.offer(value)
                elif bridge.error:
                    with pipeline.lock:
                        pipeline.metric_cache = dict(
                            pipeline.metric_cache, metrics_error=bridge.error
                        )
                due += 1.0
                pipeline.stop.wait(max(0.0, due - time.monotonic()))
                if due < time.monotonic() - 1.0:
                    due = time.monotonic()
        except Exception as exc:
            with pipeline.lock:
                pipeline.metric_cache = dict(
                    pipeline.metric_cache, metrics_error=str(exc)
                )
        finally:
            bridge.close()

    pipeline.metrics_thread = threading.Thread(
        target=worker, name="inference-metrics", daemon=True
    )
    pipeline.metrics_thread.start()


def snapshot(pipeline, now):
    cache = pipeline.metric_cache or empty(now)
    return dict(
        cache,
        metrics_age_ms=(now - cache["counters_monotonic"]) * 1000,
        effective_result_count=pipeline.published_count,
        effective_last_source=pipeline.effective_source,
        effective_source_gaps=pipeline.effective_source_gaps,
        duplicate_results=pipeline.duplicate_results,
        capture_count=pipeline.capture_count,
        published_count=pipeline.published_count,
        stale_results=pipeline.stale_results,
        queue=pipeline.scheduler.snapshot() if pipeline.scheduler else None,
        worker_scheduling=dict(pipeline.worker_scheduling),
        model_input_size=(
            pipeline.config.get("input_size", [512, 288]) if pipeline.config else None
        ),
        input_transform=(
            pipeline.config.get("input_transform") if pipeline.config else None
        ),
        metrics_compute_outside_pipeline_lock=True,
        model_calls_and_skipped_sources_are_separate=True,
        statistics_process=pipeline.metrics_process.snapshot(),
    )
