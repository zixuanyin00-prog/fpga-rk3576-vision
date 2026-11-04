import threading
import time
from runtime_timing import summary, engine_phases

FIELDS = (
    "queue_wait_ms",
    "preprocessing_ms",
    "input_copy_ms",
    "decode_ms",
    "rknn_ms",
    "received_to_result_ms",
)


def empty(now):
    return dict(
        counters_monotonic=now,
        cores=[
            dict(
                core=c,
                calls=0,
                calls_per_second=0,
                **{field: summary([]) for field in FIELDS}
            )
            for c in (0, 1)
        ],
        capture_wait_ms=summary([]),
        received_to_ordered_result_ms=summary([]),
    )


def start(pipeline):
    if getattr(pipeline, "metrics_thread", None):
        return
    pipeline.metric_cache = empty(time.monotonic())

    def worker():
        while not pipeline.stop.is_set():
            try:
                with pipeline.lock:
                    now = time.monotonic()
                    rows = [list(s) for s in pipeline.core_samples]
                    calls = list(pipeline.core_calls)
                    capture = list(pipeline.capture_wait)
                    ordered = list(pipeline.ordered_latency)
                    model_sha = pipeline.model_sha256
                cores = []
                for core in (0, 1):
                    samples = [s for s in rows[core] if s["time"] > now - 30]
                    cores.append(
                        dict(
                            core=core,
                            model_sha256=model_sha,
                            calls=calls[core],
                            calls_per_second=sum(s["time"] > now - 2 for s in samples)
                            / 2,
                            engine_phases=engine_phases(samples),
                            **{
                                field: summary([s[field] for s in samples])
                                for field in FIELDS
                            }
                        )
                    )
                cache = dict(
                    counters_monotonic=now,
                    cores=cores,
                    capture_wait_ms=summary([v for t, v in capture if t > now - 30]),
                    received_to_ordered_result_ms=summary(
                        [v for t, v in ordered if t > now - 30]
                    ),
                )
                with pipeline.lock:
                    pipeline.metric_cache = cache
            except Exception as exc:
                with pipeline.lock:
                    pipeline.metric_cache = dict(
                        pipeline.metric_cache, metrics_error=str(exc)
                    )
            pipeline.stop.wait(1)

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
        recent_ordered_results=list(pipeline.trace)[-12:],
    )
