import json
import time
from dynamic_audit import DynamicAudit


class LeasedDynamicAudit(DynamicAudit):
    def __init__(self, root, lease):
        super().__init__(root)
        self.lease = lease
        self.token = None
        self.deadline = 0

    def _release(self):
        if self.token is not None:
            self.lease.release(self.token)
            self.token = None

    def _terminal(self):
        super()._terminal()
        if self.request is None:
            self._release()

    def tick(self):
        with self.lock:
            if self.request and time.monotonic() > self.deadline:
                self.state = "exhausted"
                self.attempts = self.request["max_attempts"]
                self._status()
                self._terminal()

    def offer(self, frame, objects, key, decisions, done, model_sha256, context=None):
        with self.lock:
            self.tick()
            path = self.root / "request.json"
            if (
                self.request is None
                and path.exists()
                and time.monotonic() >= self.next_poll
            ):
                spec = json.loads(path.read_text())
                token = self.lease.acquire("dynamic_audit", spec.get("id", ""))
                if token is None:
                    return
                self.token = token
                try:
                    self.deadline = (
                        time.monotonic()
                        + spec.get("max_attempts", 45) / max(0.2, spec.get("hz", 0.2))
                        + 60
                    )
                except Exception:
                    self._release()
                    raise
            try:
                super().offer(
                    frame, objects, key, decisions, done, model_sha256, context=context
                )
            except Exception:
                if self.request is None:
                    self._release()
                raise

    def close(self):
        super().close()
        with self.lock:
            self._release()
