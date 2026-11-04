from collections import deque
import time
import numpy as np

from runtime_timing import summary

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
    "native_ring_wait_ms",
    "python_queue_wait_ms",
    "admission_wait_ms",
    "worker_dispatch_wait_ms",
    "npu_worker_wall_ms",
    "result_completed_to_collector_ms",
    "result_to_collector_ms",
    "queue_wait_ms",
    "rknn_ms",
    "received_to_result_ms",
)
SCHEDULER_FIELDS = (
    "pending_wait_ms",
    "worker_execution_ms",
    "completed_waiting_ms",
    "publication_callback_ms",
    "occupied_residence_ms",
)
ENGINE_FIELDS = (
    "inputs_set_ms",
    "run_ms",
    "outputs_get_ms",
    "convert_ms",
    "release_ms",
    "total_ms",
)


def start(pipeline, reset=False):
    if getattr(pipeline, "minimal_metric_state", None) is not None and not reset:
        return
    pipeline.minimal_metric_state = dict(
        publications=deque(maxlen=120),
        next_summary=0.0,
        cache={},
        token=pipeline._token(),
        rate_calls=None,
        rate_core_clock=None,
        rate_capture=None,
        rate_capture_clock=None,
        rate_published=pipeline.published_count,
        rate_effective_clock=time.monotonic(),
    )


def record_publication(pipeline, sample):

    pipeline.minimal_metric_state["publications"].append(
        (
            sample["source_frame_id"],
            sample["final_result_publication_monotonic"],
            tuple(sample.get(field) for field in PUBLISH_FIELDS),
        )
    )


def capture(pipeline, now):

    if getattr(pipeline, "minimal_metric_state", None) is None:
        start(pipeline)
    if pipeline.minimal_metric_state["token"] != pipeline._token():

        start(pipeline, reset=True)
    state = pipeline.minimal_metric_state
    refresh = now >= state["next_summary"]
    published = pipeline.published_count
    effective_clock = time.monotonic()
    publications = tuple(state["publications"]) if refresh else None
    counters = dict(
        effective_result_count=published,
        effective_last_source=pipeline.effective_source,
        effective_source_gaps=pipeline.effective_source_gaps,
        duplicate_results=pipeline.duplicate_results,
        published_count=published,
        stale_results=pipeline.stale_results,
        model_input_size=(
            pipeline.config.get("input_size", [512, 288]) if pipeline.config else None
        ),
        input_transform=(
            pipeline.config.get("input_transform") if pipeline.config else None
        ),
    )
    return dict(
        state=state,
        cache=state["cache"],
        token=state["token"],
        model_sha256=pipeline.model_sha256,
        refresh=refresh,
        published=published,
        effective_clock=effective_clock,
        publications=publications,
        counters=counters,
        scheduler=pipeline.scheduler,
        config=pipeline.config,
        rate_calls=state["rate_calls"],
        rate_core_clock=state["rate_core_clock"],
        rate_capture=state["rate_capture"],
        rate_capture_clock=state["rate_capture_clock"],
        rate_published=state["rate_published"],
        rate_effective_clock=state["rate_effective_clock"],
    )


def summarize(pipeline, data):

    state = data["state"]
    cache = data["cache"]
    refresh = data["refresh"]
    with pipeline.sample_lock:
        calls = tuple(pipeline.core_calls)
        core_clock = time.monotonic()
        last_samples = (
            tuple(rows[-1] if rows else None for rows in pipeline.core_samples)
            if refresh
            else None
        )
        worker_scheduling = dict(pipeline.worker_scheduling)
    with pipeline.capture_lock:
        capture = pipeline.capture_count
        capture_clock = time.monotonic()
        last_capture_wait = (
            pipeline.capture_wait[-1][1] if refresh and pipeline.capture_wait else None
        )
    published = data["published"]
    effective_clock = data["effective_clock"]
    queue = None
    if refresh:
        scheduler = data["scheduler"]
        queue = None
        if scheduler is not None:
            with scheduler.cv:
                publishing = scheduler.publishing_key is not None
                queue = dict(
                    capacity=scheduler.capacity,
                    pending=len(scheduler.pending),
                    running=len(scheduler.active),
                    awaiting_order=len(scheduler.order),
                    completed_waiting=len(scheduler.results) - int(publishing),
                    high_water=scheduler.high_water,
                    dropped=scheduler.dropped,
                    cancelled=scheduler.cancelled,
                    published=scheduler.published,
                    epoch=scheduler.epoch,
                    pending_depth=len(scheduler.pending),
                    occupied_depth=len(set(scheduler.order) | scheduler.active),
                    publishing=publishing,
                    worker_capacity=2,
                    admitted_count=scheduler.admitted_count,
                    started_count=scheduler.started_count,
                    execute_completed_count=scheduler.execute_completed_count,
                    valid_completed_count=scheduler.valid_completed_count,
                    rejected_no_pending=scheduler.rejected_no_pending,
                    replaced_pending=scheduler.replaced_pending,
                    skipped_source_count=scheduler.rejected_no_pending
                    + scheduler.replaced_pending
                    + scheduler.admission_timeouts,
                    admission_policy=(
                        "bounded_fifo_stop_epoch_wait"
                        if scheduler.minimal_fifo
                        else "nonblocking_replace_oldest_unstarted_pending"
                    ),
                    admission_timeouts=scheduler.admission_timeouts,
                    admission_rejected_epoch=scheduler.admission_rejected_epoch,
                    admission_rejected_closed=scheduler.admission_rejected_closed,
                    admission_wait_count=scheduler.admission_wait_count,
                    admission_wait_ms_total=scheduler.admission_wait_ms_total,
                    admission_wait_ms_max=scheduler.admission_wait_ms_max,
                    collector_scheduling=getattr(
                        scheduler, "collector_scheduling", None
                    ),
                    input_transform=(
                        data["config"].get("input_transform")
                        if data["config"]
                        else None
                    ),
                    model_input_size=(
                        data["config"].get("input_size") if data["config"] else None
                    ),
                    partial_tile_results=False,
                    complete_source_publication=True,
                    published_is_callback_count=True,
                    counters_monotonic=time.monotonic(),
                    diagnostics="compact queue counters; no scheduler history scan",
                )
    if refresh:
        started = time.monotonic()
        cpu_started = time.thread_time()
        core_seconds = (
            core_clock - data["rate_core_clock"]
            if data["rate_core_clock"] is not None
            else 0.0
        )
        capture_seconds = (
            capture_clock - data["rate_capture_clock"]
            if data["rate_capture_clock"] is not None
            else 0.0
        )
        effective_seconds = effective_clock - data["rate_effective_clock"]
        cores = []
        for core, row in enumerate(last_samples):
            fields = {
                field: summary([row[field]]) if row is not None else summary([])
                for field in FIELDS
            }
            engine = row.get("engine_timing") if row is not None else None
            phases = dict(
                samples=int(engine is not None),
                phases={
                    field: (
                        summary([engine[field]]) if engine is not None else summary([])
                    )
                    for field in ENGINE_FIELDS
                },
                python_bridge_gap_ms=(
                    summary([row["rknn_ms"] - engine["total_ms"]])
                    if engine is not None
                    else summary([])
                ),
                last_engine_sequence=engine["sequence"] if engine is not None else None,
            )
            cores.append(
                dict(
                    core=core,
                    model_sha256=data["model_sha256"],
                    calls_per_second=(
                        (calls[core] - data["rate_calls"][core]) / core_seconds
                        if core_seconds > 0
                        else 0.0
                    ),
                    last_call_source_frame_id=(
                        row["source_frame_id"] if row is not None else None
                    ),
                    last_call_monotonic=row["time"] if row is not None else None,
                    timing_scope="last completed model call on this core",
                    engine_phases=phases,
                    **fields
                )
            )

        rows = data["publications"]
        if rows:

            values = np.sort(
                np.asarray([row[2] for row in rows], dtype=np.float64), axis=0
            )
            counts = np.count_nonzero(~np.isnan(values), axis=0)
            columns = np.arange(len(PUBLISH_FIELDS))
            last = np.maximum(counts, 1) - 1
            ranks = last[None, :] * np.asarray((0.5, 0.95))[:, None]
            lower = ranks.astype(np.intp)
            upper = np.minimum(lower + 1, last)
            low = values[lower, columns]
            percentiles = low + (values[upper, columns] - low) * (ranks - lower)
            means = np.nansum(values, axis=0) / np.maximum(counts, 1)
            maxima = values[last, columns]
            publication = {
                field: (
                    dict(
                        samples=int(counts[index]),
                        mean=float(means[index]),
                        p50=float(percentiles[0, index]),
                        p95=float(percentiles[1, index]),
                        max=float(maxima[index]),
                    )
                    if counts[index]
                    else summary([])
                )
                for index, field in enumerate(PUBLISH_FIELDS)
            }
        else:
            publication = {field: summary([]) for field in PUBLISH_FIELDS}

        total_index = PUBLISH_FIELDS.index("received_to_publish_ms")
        slowest = sorted(
            (row for row in rows if row[2][total_index] is not None),
            key=lambda row: row[2][total_index],
            reverse=True,
        )[:5]
        slowest = [
            dict(
                source_frame_id=row[0],
                final_publication_monotonic=row[1],
                phases=dict(zip(PUBLISH_FIELDS, row[2])),
            )
            for row in slowest
        ]
        ids = [row[0] for row in rows]
        steps = [(b - a) & 0xFFFFFFFF for a, b in zip(ids, ids[1:])]
        continuity = dict(
            samples=len(ids),
            unique_sources=len(set(ids)),
            duplicate_sources=len(ids) - len(set(ids)),
            source_gaps=sum(step - 1 for step in steps if 0 < step < 0x80000000),
            nonforward_steps=sum(not 0 < step < 0x80000000 for step in steps),
            first_source_frame_id=ids[0] if ids else None,
            last_source_frame_id=ids[-1] if ids else None,
            first_final_publish_monotonic=rows[0][1] if rows else None,
            last_final_publish_monotonic=rows[-1][1] if rows else None,
            scope="last at most 120 final committed source publications",
        )
        total = publication["received_to_publish_ms"]
        cache = dict(
            cores=cores,
            publication=publication,
            publication_sources=continuity,
            slowest_source_publications=slowest,
            slowest_source_scope="five largest receive-to-publication delays among the same bounded source window",
            capture_wait_ms=(
                summary([last_capture_wait])
                if last_capture_wait is not None
                else summary([])
            ),
            received_to_ordered_result_ms=total,
            scheduler_phase_timing={field: summary([]) for field in SCHEDULER_FIELDS},
            scheduler_phase_scope="disabled in minimal60; no phase history scan",
            recent_ordered_results=[],
            queue=queue,
            publication_target=dict(
                p95_target_ms=100.0,
                measured_samples=total["samples"],
                p95_ms=total["p95"],
                under_100ms_p95=total["p95"] is not None and total["p95"] <= 100.0,
                scope="validated DMA receipt to final source publication commit",
                endpoint_is_python_function_return=False,
                physical_exposure_timestamp_available=False,
                ui_state_publication_is_separate=True,
            ),
            capture_per_second=(
                (capture - data["rate_capture"]) / capture_seconds
                if capture_seconds > 0
                else 0.0
            ),
            effective_results_per_second=(
                (published - data["rate_published"]) / effective_seconds
                if effective_seconds > 0
                else 0.0
            ),
            rate_counters_monotonic=core_clock,
            capture_rate_counters_monotonic=capture_clock,
            effective_rate_counters_monotonic=effective_clock,
            rate_window_seconds=core_seconds,
            summaries_monotonic=time.monotonic(),
            timing_history_limit=120,
            statistics_backend="bounded scalar summaries outside publication lock every 0.5s",
            metrics_compute_wall_ms=(time.monotonic() - started) * 1000,
            metrics_compute_cpu_ms=(time.thread_time() - cpu_started) * 1000,
        )
        with pipeline.lock:
            if (
                pipeline.minimal_metric_state is state
                and pipeline._token() == data["token"]
            ):
                state.update(
                    cache=cache,
                    rate_calls=calls,
                    rate_core_clock=core_clock,
                    rate_capture=capture,
                    rate_capture_clock=capture_clock,
                    rate_published=published,
                    rate_effective_clock=effective_clock,
                    next_summary=core_clock + 0.5,
                )

    return dict(
        cache,
        cores=[dict(row, calls=calls[core]) for core, row in enumerate(cache["cores"])],
        counters_monotonic=core_clock,
        capture_counters_monotonic=capture_clock,
        effective_counters_monotonic=effective_clock,
        metrics_age_ms=max(
            0.0, (effective_clock - cache["summaries_monotonic"]) * 1000
        ),
        **data["counters"],
        capture_count=capture,
        worker_scheduling=worker_scheduling,
        metrics_compute_outside_pipeline_lock=True,
        model_calls_and_skipped_sources_are_separate=True,
        statistics_process=dict(
            backend="disabled_minimal60",
            pid=None,
            error=None,
            worker_thread=False,
            history_copied=False,
        )
    )


def snapshot(pipeline, now):

    with pipeline.lock:
        data = capture(pipeline, now)
    return summarize(pipeline, data)
