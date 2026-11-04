from collections import deque
import threading
import time


class OrderedInference:
    def __init__(self, execute, publish, fail, capacity=6, setup=None):
        if capacity < 2:
            raise ValueError("Capacity must accommodate both workers")
        self.execute, self.publish, self.fail, self.setup = (
            execute,
            publish,
            fail,
            setup,
        )
        self.capacity = capacity
        self.cv = threading.Condition()
        self.pending, self.order = deque(), deque()
        self.results = {}
        self.epoch = self.sequence = 0
        self.closed = False
        self.active = set()
        self.dropped = self.cancelled = self.published = 0
        self.high_water = 0
        self.threads = [
            threading.Thread(
                target=self._worker, args=(core,), name=f"fine-core{core}", daemon=True
            )
            for core in (0, 1)
        ]
        self.threads.append(
            threading.Thread(
                target=self._collect, name="fine-ordered-results", daemon=True
            )
        )

    def start(self):
        for thread in self.threads:
            thread.start()

    def invalidate(self):
        with self.cv:
            self.epoch += 1
            self.cancelled += len(self.order)
            self.pending.clear()
            self.order.clear()
            self.results.clear()
            self.cv.notify_all()

    def submit(self, job):
        with self.cv:
            if self.closed:
                return False

            occupied = len(set(self.order) | self.active)
            if occupied >= self.capacity:
                if not self.pending:
                    self.dropped += 1
                    return False
                old, _ = self.pending.popleft()
                self.order.remove(old)
                self.dropped += 1
            self.sequence += 1
            key = (self.epoch, self.sequence)
            self.order.append(key)
            self.pending.append((key, job))
            self.high_water = max(self.high_water, len(set(self.order) | self.active))
            self.cv.notify_all()
            return True

    def _worker(self, core):
        try:
            if self.setup:
                self.setup(core)
            while True:
                with self.cv:
                    self.cv.wait_for(lambda: self.closed or self.pending)
                    if self.closed:
                        return
                    key, job = self.pending.popleft()
                    self.active.add(key)
                result = self.execute(core, job)
                with self.cv:
                    self.active.discard(key)
                    if key[0] == self.epoch and key in self.order:
                        self.results[key] = (job, result)
                    self.cv.notify_all()
        except BaseException as exc:
            self.fail(exc)
            self.stop()

    def _collect(self):
        try:
            while True:
                with self.cv:
                    self.cv.wait_for(
                        lambda: self.closed
                        or (self.order and self.order[0] in self.results)
                    )
                    if self.closed:
                        return
                    key = self.order.popleft()
                    job, result = self.results.pop(key)

                if result is not None:
                    self.publish(job, result)
                    with self.cv:
                        self.published += 1
        except BaseException as exc:
            self.fail(exc)
            self.stop()

    def snapshot(self):
        with self.cv:
            return dict(
                capacity=self.capacity,
                pending=len(self.pending),
                running=len(self.active),
                awaiting_order=len(self.order),
                completed_waiting=len(self.results),
                high_water=self.high_water,
                dropped=self.dropped,
                cancelled=self.cancelled,
                published=self.published,
                epoch=self.epoch,
            )

    def stop(self):
        with self.cv:
            self.closed = True
            self.pending.clear()
            self.order.clear()
            self.results.clear()
            self.cv.notify_all()

    def close(self):
        self.stop()
        for thread in self.threads:
            if thread.ident is not None:
                thread.join(10)
            if thread.is_alive():
                raise RuntimeError("Inference worker failed to stop: " + thread.name)
