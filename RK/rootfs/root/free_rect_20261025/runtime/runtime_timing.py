import math


def summary(values):
    values = sorted(values)
    n = len(values)
    if not n:
        return dict(samples=0, mean=None, p50=None, p95=None, max=None)

    def percentile(fraction):
        index = (n - 1) * fraction
        lower = int(index)
        upper = min(lower + 1, n - 1)
        weight = index - lower

        return float(values[lower] + (values[upper] - values[lower]) * weight)

    return dict(
        samples=n,
        mean=math.fsum(values) / n,
        p50=percentile(0.5),
        p95=percentile(0.95),
        max=float(values[-1]),
    )


def engine_phases(samples):

    measured = [sample for sample in samples if "engine_timing" in sample]
    keys = (
        "inputs_set_ms",
        "run_ms",
        "outputs_get_ms",
        "convert_ms",
        "release_ms",
        "total_ms",
    )
    return dict(
        samples=len(measured),
        phases={
            key: summary([sample["engine_timing"][key] for sample in measured])
            for key in keys
        },
        python_bridge_gap_ms=summary(
            [
                sample["rknn_ms"] - sample["engine_timing"]["total_ms"]
                for sample in measured
            ]
        ),
        last_engine_sequence=(
            measured[-1]["engine_timing"]["sequence"] if measured else None
        ),
    )
