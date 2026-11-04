import os
import pickle
import sys
import time
from metric_columns import one_summary as summary, core_summaries, fixed_summaries

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


def compute(value):
    started = time.monotonic()
    cpu_started = time.thread_time()
    if value.get("wire_version") == 1:
        rows = []
        for packed_rows in value["rows"]:
            group = []
            for fields, engine in packed_rows:
                row = dict(zip(CORE_WIRE_FIELDS, fields))
                if engine is not None:
                    row["engine_timing"] = dict(zip(ENGINE_FIELDS, engine))
                group.append(row)
            rows.append(group)
        value = dict(
            value,
            rows=rows,
            trace=[dict(zip(TRACE_WIRE_FIELDS, row)) for row in value["trace"]],
            scheduler=[dict(zip(SCHEDULER_FIELDS, row)) for row in value["scheduler"]],
        )
    now = value["now"]
    rows = value["rows"]
    calls = value["calls"]
    trace = value["trace"]
    cores = []
    for core in (0, 1):
        samples = [s for s in rows[core] if s["time"] > now - 30]
        fields, phases = core_summaries(samples, FIELDS)
        cores.append(
            dict(
                core=core,
                model_sha256=value["model_sha"],
                calls=calls[core],
                calls_per_second=sum(s["time"] > now - 2 for s in samples) / 2,
                engine_phases=phases,
                **fields
            )
        )
    published = [
        s for s in trace if s.get("result_callback_completed_monotonic", 0) > now - 30
    ]
    ids = [int(s["source_frame_id"]) for s in published]
    steps = [(b - a) & 0xFFFFFFFF for a, b in zip(ids, ids[1:])]
    source_continuity = dict(
        samples=len(ids),
        unique_sources=len(set(ids)),
        duplicate_sources=len(ids) - len(set(ids)),
        source_gaps=sum(step - 1 for step in steps if 0 < step < 0x80000000),
        nonforward_steps=sum(not 0 < step < 0x80000000 for step in steps),
        first_source_frame_id=ids[0] if ids else None,
        last_source_frame_id=ids[-1] if ids else None,
        first_final_publish_monotonic=(
            published[0]["final_result_publication_monotonic"] if ids else None
        ),
        last_final_publish_monotonic=(
            published[-1]["final_result_publication_monotonic"] if ids else None
        ),
        scope="every retained complete source publication in 30s rolling window; no predicted votes",
    )
    publication = fixed_summaries(published, PUBLISH_FIELDS)
    total = publication["received_to_publish_ms"]
    return dict(
        counters_monotonic=now,
        cores=cores,
        publication_sources=source_continuity,
        capture_wait_ms=summary([v for t, v in value["capture"] if t > now - 30]),
        received_to_ordered_result_ms=summary(
            [v for t, v in value["ordered"] if t > now - 30]
        ),
        publication=publication,
        scheduler_phase_timing=fixed_summaries(value["scheduler"], SCHEDULER_FIELDS),
        scheduler_phase_scope="30s completed execute callbacks, ordered waiting and publication; actual NPU timings are separate",
        publication_target=dict(
            p95_target_ms=100.0,
            measured_samples=total["samples"],
            p95_ms=total["p95"],
            under_100ms_p95=total["p95"] is not None and total["p95"] <= 100.0,
            scope="validated_DMA_receipt_to_final_publication_commit_including_both_state_lock_waits",
            endpoint_is_python_function_return=False,
            physical_exposure_timestamp_available=False,
            ui_state_publication_is_separate=True,
        ),
        recent_ordered_results=value.get("recent_trace", trace[-12:]),
        statistics_backend="numpy_batched_float64_columns_longdouble_mean_linear_percentiles",
        metrics_compute_wall_ms=(time.monotonic() - started) * 1000,
        metrics_compute_cpu_ms=(time.thread_time() - cpu_started) * 1000,
        metrics_snapshot_cpu_ms=value.get("snapshot_cpu_ms", 0.0),
        metrics_snapshot_wall_ms=value.get("snapshot_wall_ms", 0.0),
    )


def main():
    if sys.argv[1] == "1" and hasattr(os, "sched_setaffinity"):
        os.sched_setaffinity(0, {0, 1, 2})
        os.setpriority(os.PRIO_PROCESS, 0, 10)
    source = sys.stdin.buffer
    destination = sys.stdout.buffer
    pickle.dump(dict(ready=True), destination, protocol=5)
    destination.flush()
    while True:
        request = pickle.load(source)
        if request is None:
            pickle.dump(dict(final=True), destination, protocol=5)
            destination.flush()
            return
        try:
            response = dict(cache=compute(request))
        except Exception as exc:
            response = dict(error=str(exc))
        pickle.dump(response, destination, protocol=5)
        destination.flush()


if __name__ == "__main__":
    main()
