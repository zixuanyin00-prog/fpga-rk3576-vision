import hashlib
import io
import json
import sys
import time
import unittest
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
MODULES = (
    "test_capture_contract",
    "test_pose_feedback",
    "test_training_backend",
    "test_proposals",
    "test_gpu_policy",
)


def protected_files():
    paths = [
        ROOT / "settings.json",
        ROOT / "box_assistant/active.json",
        ROOT / "box_foundation/active.json",
    ]
    paths += list((ROOT / "data").glob("*/project.json"))
    return {
        str(p.relative_to(ROOT)): (
            hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
        )
        for p in paths
    }


class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.passed = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.passed.append(test.id())


def main():
    started = datetime.now(timezone(timedelta(hours=8)))
    clock = time.monotonic()
    before = protected_files()
    suite = unittest.defaultTestLoader.loadTestsFromNames(MODULES)
    stream = io.StringIO()
    with patch(
        "paramiko.SSHClient.connect",
        side_effect=AssertionError("real SSH forbidden in offline revalidation"),
    ), patch(
        "torch.load",
        side_effect=AssertionError(
            "historical checkpoint loading forbidden in offline revalidation"
        ),
    ), patch(
        "service.subprocess.Popen",
        side_effect=AssertionError(
            "external workers forbidden in offline revalidation"
        ),
    ):
        result = unittest.TextTestRunner(
            stream=stream, verbosity=2, resultclass=RecordingResult
        ).run(suite)
    after = protected_files()
    unchanged = before == after
    folder = ROOT / "evidence/capture_protocol_revalidation_20261025"
    folder.mkdir(parents=True, exist_ok=True)
    name = started.strftime("%Y%m%d_%H%M%S_%f")
    log = folder / (name + ".log")
    log.write_text(stream.getvalue(), encoding="utf-8")
    sources = [
        "tests/capture_simulator.py",
        "tests/test_capture_contract.py",
        "tests/revalidate_capture.py",
        "board/dynamic_audit.py",
        "service.py",
        "core/environment15.py",
        "core/capture.py",
        "core/batch15.py",
    ]
    report = dict(
        started_at_beijing=started.isoformat(),
        seconds=time.monotonic() - clock,
        scope="offline PC simulation of current v15 sampling backend and feedback regression; not live-board acceptance",
        protocol="native-effective15-v1",
        actual_board_backend_exercised_locally="board/dynamic_audit.py",
        tests_run=result.testsRun,
        passed=len(result.passed),
        failures=[dict(test=t.id(), trace=trace) for t, trace in result.failures],
        errors=[dict(test=t.id(), trace=trace) for t, trace in result.errors],
        skipped=[dict(test=t.id(), reason=reason) for t, reason in result.skipped],
        per_module_passed=dict(Counter(t.split(".")[0] for t in result.passed)),
        passed_tests=result.passed,
        success=result.wasSuccessful() and unchanged,
        protected_user_files_unchanged=unchanged,
        protected_files_before=before,
        protected_files_after=after,
        guards=dict(
            real_ssh_forbidden=True,
            checkpoint_loading_forbidden=True,
            external_workers_forbidden=True,
        ),
        board_updated=False,
        model_applied=False,
        board_current_60fps_verified=False,
        field_accuracy_verified=False,
        source_sha256={
            p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in sources
        },
        log=str(log),
        selection=list(MODULES),
        full_test_discovery=False,
    )
    for path in (folder / (name + ".json"), folder / "latest.json"):
        path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "success",
                    "tests_run",
                    "passed",
                    "per_module_passed",
                    "protected_user_files_unchanged",
                )
            },
            ensure_ascii=False,
        )
    )
    print("Evidence: " + str(folder / "latest.json"))
    if not report["success"]:
        print(stream.getvalue())
        raise SystemExit(1)


if __name__ == "__main__":
    main()
