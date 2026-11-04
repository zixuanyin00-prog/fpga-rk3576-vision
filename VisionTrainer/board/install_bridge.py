from pathlib import Path
import sys
import json
import time
import datetime
import hashlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent / "preview60_live_20261023"))
from board import connect, run, REMOTE, PY

c = connect()
s = c.open_sftp()
stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
backup = REMOTE + "/backup_training_bridge_" + stamp
try:
    before = json.loads(s.open(REMOTE + "/runtime_status.json").read())
    run(c, "mkdir -p " + backup)
    for name in ["focused_inference.py", "focus_region.json"]:
        s.get(REMOTE + "/" + name, str(ROOT / "evidence" / ("before_bridge_" + name)))
        run(c, f"cp -p {REMOTE}/{name} {backup}/{name}")
    for name in ["focused_inference.py", "training_capture_bridge.py"]:
        s.put(str(ROOT / "board" / name), backup + "/" + name + ".candidate")
    run(
        c,
        f"{PY} -m py_compile {backup}/focused_inference.py.candidate {backup}/training_capture_bridge.py.candidate",
    )
    started = time.monotonic()
    run(c, "systemctl stop fpga-vision-v3.service")
    try:
        for name in ["focused_inference.py", "training_capture_bridge.py"]:
            run(c, f"cp {backup}/{name}.candidate {REMOTE}/{name}")
        run(c, "systemctl start fpga-vision-v3.service")
        pid = int(run(c, "systemctl show fpga-vision-v3.service -p MainPID --value"))
        for _ in range(20):
            time.sleep(1)
            after = json.loads(s.open(REMOTE + "/runtime_status.json").read())
            now = float(s.open("/proc/uptime").read(64).split()[0])
            if (
                after["pid"] == pid
                and now - after["recorded_monotonic"] < 2
                and after["inference"]["captured"] > 10
            ):
                break
        else:
            raise RuntimeError("New runtime did not become healthy")
        assert before["inference"]["model_sha256"] == after["inference"]["model_sha256"]
    except Exception:
        run(c, "systemctl stop fpga-vision-v3.service")
        run(c, f"cp {backup}/focused_inference.py {REMOTE}/focused_inference.py")
        run(c, "systemctl start fpga-vision-v3.service")
        raise
    record = dict(
        installed_at=datetime.datetime.now().astimezone().isoformat(),
        backup=backup,
        previous_pid=before["pid"],
        pid=pid,
        switch_seconds=time.monotonic() - started,
        continuous_display_during_switch=False,
        fpga_changed=False,
        driver_changed=False,
        model_changed=False,
        model_sha256=after["inference"]["model_sha256"],
        display_acceptance_pending=True,
        files={
            n: hashlib.sha256((ROOT / "board" / n).read_bytes()).hexdigest()
            for n in ["focused_inference.py", "training_capture_bridge.py"]
        },
    )
    (ROOT / "evidence/bridge_deployment.json").write_text(json.dumps(record, indent=2))
    print(json.dumps(record, indent=2))
finally:
    s.close()
    c.close()
