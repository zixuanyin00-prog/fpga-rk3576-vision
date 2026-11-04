from operator import itemgetter
import numpy as np


def matrix_summaries(matrix, names):

    n = len(matrix)
    if not n:
        return {
            name: dict(samples=0, mean=None, p50=None, p95=None, max=None)
            for name in names
        }
    data = np.array(matrix, dtype=np.float64, copy=True).reshape(n, len(names))
    data.sort(axis=0)

    means = np.asarray(data.sum(axis=0, dtype=np.longdouble) / n, dtype=np.float64)
    positions = (n - 1) * np.asarray((0.5, 0.95), dtype=np.float64)
    lower = positions.astype(np.intp)
    upper = np.minimum(lower + 1, n - 1)
    quantiles = data[lower] + (data[upper] - data[lower]) * (positions - lower)[:, None]
    return {
        name: dict(
            samples=n,
            mean=float(means[index]),
            p50=float(quantiles[0, index]),
            p95=float(quantiles[1, index]),
            max=float(data[-1, index]),
        )
        for index, name in enumerate(names)
    }


def fixed_summaries(rows, names):

    if not rows:
        return matrix_summaries([], names)
    return matrix_summaries(list(map(itemgetter(*names), rows)), names)


def one_summary(values):
    return matrix_summaries(
        np.asarray(values, dtype=np.float64).reshape(-1, 1), ("value",)
    )["value"]


def core_summaries(samples, fields):
    measured = [sample for sample in samples if "engine_timing" in sample]
    names = (
        "inputs_set_ms",
        "run_ms",
        "outputs_get_ms",
        "convert_ms",
        "release_ms",
        "total_ms",
    )
    get_phases = itemgetter(*names)
    rows = [
        get_phases(sample["engine_timing"])
        + (sample["rknn_ms"] - sample["engine_timing"]["total_ms"],)
        for sample in measured
    ]
    columns = matrix_summaries(rows, names + ("python_bridge_gap_ms",))
    return fixed_summaries(samples, fields), dict(
        samples=len(measured),
        phases={name: columns[name] for name in names},
        python_bridge_gap_ms=columns["python_bridge_gap_ms"],
        last_engine_sequence=(
            measured[-1]["engine_timing"]["sequence"] if measured else None
        ),
    )
