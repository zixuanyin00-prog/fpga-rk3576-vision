from collections import deque

VERSION = 2
TERMINAL_FIELDS = (
    "final_unconfirmed",
    "identity_lost_unconfirmed",
    "interrupted_unconfirmed",
    "identity_lost_confirmed",
)


def ensure(tracker):
    confirmed = sum(max(0, int(v)) for v in tracker.counts.values())
    if not hasattr(tracker, "observed_total"):
        tracker.observed_total = confirmed
    if not hasattr(tracker, "passage_sequence"):
        tracker.passage_sequence = 0
        tracker.passage_updates = deque(maxlen=256)
    if getattr(tracker, "counting_semantics_version", None) != VERSION:
        tracker.counting_semantics_version = VERSION
        tracker.legacy_unresolved = max(0, int(tracker.observed_total) - confirmed)
        for name in TERMINAL_FIELDS:
            setattr(tracker, name, 0)


def emit(tracker, key, track, now):
    ensure(tracker)
    if not track.get("observed_registered") or "registered_at" not in track:
        return
    state = track.get("ledger_terminal_state") or (
        "confirmed" if track.get("counted") else "pending"
    )
    tracker.passage_sequence += 1
    tracker.passage_updates.append(
        [
            tracker.passage_sequence,
            str(getattr(tracker, "region_version", "")) + ":" + str(key),
            track["registered_at"],
            track.get("counted_label") if track.get("counted") else None,
            float(now),
            state,
        ]
    )


def retire(tracker, key, track, kind, now):
    ensure(tracker)
    if not track.get("observed_registered") or track.get("ledger_terminal_state"):
        return
    if track.get("counted"):
        if kind == "identity_lost":
            tracker.identity_lost_confirmed += 1
    else:
        field = {
            "final_unconfirmed": "final_unconfirmed",
            "identity_lost": "identity_lost_unconfirmed",
            "observation_interrupted": "interrupted_unconfirmed",
        }.get(kind)
        if field:
            setattr(tracker, field, getattr(tracker, field) + 1)
    track["ledger_terminal_state"] = (
        "confirmed"
        if track.get("counted")
        else {
            "final_unconfirmed": "final_unconfirmed",
            "identity_lost": "identity_lost",
            "observation_interrupted": "interrupted",
        }.get(kind, "interrupted")
    )
    emit(tracker, key, track, now)


def summary(tracker):
    ensure(tracker)
    confirmed = sum(max(0, int(v)) for v in tracker.counts.values())
    pending = sum(
        bool(
            t.get("observed_registered")
            and not t.get("counted")
            and not t.get("ledger_terminal_state")
        )
        for t in tracker.tracks.values()
    )
    partition = dict(
        confirmed=confirmed,
        pending=pending,
        final_unconfirmed=tracker.final_unconfirmed,
        identity_lost_unconfirmed=tracker.identity_lost_unconfirmed,
        interrupted_unconfirmed=tracker.interrupted_unconfirmed,
        legacy_unresolved=tracker.legacy_unresolved,
    )
    return dict(
        counting_semantics_version=VERSION,
        observed_total=int(tracker.observed_total),
        confirmed_total=confirmed,
        confirmed_abc_total=confirmed,
        unrecognized=tracker.legacy_unresolved + tracker.final_unconfirmed,
        pending=pending,
        identity_lost_unconfirmed=tracker.identity_lost_unconfirmed,
        identity_lost_confirmed=tracker.identity_lost_confirmed,
        interrupted_unconfirmed=tracker.interrupted_unconfirmed,
        final_unconfirmed=tracker.final_unconfirmed,
        legacy_unresolved=tracker.legacy_unresolved,
        accounting_partition=partition,
        accounting_conserved=sum(partition.values()) == tracker.observed_total,
        observed_total_scope="cumulative_registered_pixel_supported_identities",
        unknown_policy="legacy_unresolved_plus_observed_departure_without_confirmation",
        unknown_includes_temporary_pending=False,
        unknown_includes_identity_loss=False,
        unknown_includes_completely_missed_objects=False,
        unknown_includes_undetected_high_confidence_errors=False,
        passage_stream=dict(
            version=VERSION,
            sequence=tracker.passage_sequence,
            updates=list(tracker.passage_updates),
            definition="independent image-supported identity registration, state revisions on same object",
        ),
    )


def restore(tracker, record):
    ensure(tracker)
    ledger = record.get("passage_ledger")
    if not ledger or ledger.get("counting_semantics_version") != VERSION:
        return
    for name in TERMINAL_FIELDS + ("pending", "legacy_unresolved"):
        if type(ledger.get(name, 0)) is not int or ledger.get(name, 0) < 0:
            raise ValueError("Invalid restored passage ledger counter: " + name)
    for name in TERMINAL_FIELDS:
        setattr(tracker, name, int(ledger.get(name, 0)))
    tracker.legacy_unresolved = int(ledger.get("legacy_unresolved", 0))

    tracker.interrupted_unconfirmed += int(ledger.get("pending", 0))
    expected = (
        sum(tracker.counts.values())
        + tracker.legacy_unresolved
        + tracker.final_unconfirmed
        + tracker.identity_lost_unconfirmed
        + tracker.interrupted_unconfirmed
    )
    if expected != tracker.observed_total:
        raise ValueError("Restored passage ledger does not conserve observed total")


def reset(tracker):
    tracker.counting_semantics_version = VERSION
    tracker.legacy_unresolved = 0
    for name in TERMINAL_FIELDS:
        setattr(tracker, name, 0)
