from pathlib import Path
import hashlib
import json
import os
import time


def restore(pipeline, selection):
    path = selection.get("counter_restore_path") if selection else None
    pipeline.counter_history_scope = "current_session"
    if not path:
        return
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != selection["counter_restore_sha256"]:
        raise ValueError("Count restore artifact hash mismatch")
    record = json.loads(raw)
    if (
        record["boot_id"] != Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        or record["run"] != selection["run"]
    ):
        raise ValueError(
            "Count restore belongs to a different boot or selected scene model"
        )
    if record["rectangle"] != pipeline.settings["rectangle"]:
        raise ValueError("Count restore ROI changed")
    labels = set(pipeline.config["labels"])
    counts = record["class_counts"]
    if set(counts) - labels or any(
        type(v) is not int or v < 0 for v in counts.values()
    ):
        raise ValueError("Invalid restored ABC counters")
    confirmed = sum(counts.values())
    observed = record.get("observed_total", confirmed)
    if type(observed) is not int or observed < confirmed:
        raise ValueError("Invalid restored observed total")
    pipeline.tracker.counts = dict(counts)
    pipeline.tracker.observed_total = observed
    import passage_ledger

    passage_ledger.restore(pipeline.tracker, record)
    pipeline.count_session = record["session_id"]
    pipeline.counter_history_scope = record.get(
        "history_scope", "pre_upgrade_only_confirmed_ABC_available"
    )


def save(pipeline):
    if not pipeline.selection:
        return
    from native_tracking import counters
    from rect_pipeline import write

    with pipeline.lock:
        ledger = counters(pipeline.tracker)
        record = dict(
            version=1,
            pid=os.getpid(),
            boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            run=pipeline.selection.get("run") if pipeline.selection else None,
            session_id=pipeline.count_session,
            rectangle=list(pipeline.settings["rectangle"]),
            class_counts=dict(pipeline.tracker.counts),
            observed_total=ledger["observed_total"],
            confirmed_abc_total=ledger["confirmed_abc_total"],
            unrecognized=ledger["unrecognized"],
            history_scope=pipeline.counter_history_scope,
            passage_ledger={
                key: ledger[key]
                for key in (
                    "counting_semantics_version",
                    "pending",
                    "final_unconfirmed",
                    "identity_lost_unconfirmed",
                    "identity_lost_confirmed",
                    "interrupted_unconfirmed",
                    "legacy_unresolved",
                )
            },
            recorded_monotonic=time.monotonic(),
        )
    write(pipeline.root / "counter_state.json", record)
