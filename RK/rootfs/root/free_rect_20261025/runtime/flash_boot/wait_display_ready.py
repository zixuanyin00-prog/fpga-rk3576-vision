import subprocess
import time

deadline = time.monotonic() + 30
while True:
    result = subprocess.run(
        ["/usr/bin/xset", "q"], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
    )
    if result.returncode == 0:
        break
    if time.monotonic() >= deadline:
        raise RuntimeError(
            "Xorg did not become ready within 30 seconds: "
            + result.stderr.decode(errors="replace")
        )
    time.sleep(0.2)
