from collections import deque
import threading
import time


class FrameTiles:
    def __init__(self, execute, publish, fail, capacity=6, setup=None):
        self.pipeline = execute.__self__
        self.publish = publish
        self.fail = fail
        self.setup = setup
        self.cv = threading.Condition()
        self.pending = None
        self.group = None
        self.closed = False
        self.epoch = 0
        self.dropped = self.cancelled = self.published = self.high_water = 0
        self.workers = [
            threading.Thread(
                target=self._worker,
                args=(i,),
                name="native-tile-core" + str(i),
                daemon=True,
            )
            for i in (0, 1)
        ]
        self.coordinator = threading.Thread(
            target=self._coordinate, name="native-frame-aggregate", daemon=True
        )

    def start(self):
        for t in self.workers + [self.coordinator]:
            t.start()

    def submit(self, job):
        with self.cv:
            if self.closed:
                return False
            if self.pending is not None:
                self.dropped += 1
            self.pending = (self.epoch, job)
            self.high_water = max(self.high_water, int(self.group is not None) + 1)
            self.cv.notify_all()
            return True

    def invalidate(self):
        with self.cv:
            self.epoch += 1
            if self.pending:
                self.cancelled += 1
                self.pending = None
            if self.group:
                g = self.group
                g["cancelled"] = True
                g["remaining"] -= len(g["tiles"])
                g["tiles"].clear()
                self.cancelled += 1
            self.cv.notify_all()

    def _worker(self, core):
        try:
            if self.setup:
                self.setup(core)
            while True:
                with self.cv:
                    self.cv.wait_for(
                        lambda: self.closed or (self.group and self.group["tiles"])
                    )
                    if self.closed:
                        return
                    group = self.group
                    tile = group["tiles"].popleft()
                    group["running"] += 1
                from console_native import infer_tile

                result = infer_tile(self.pipeline, core, group["job"], tile)
                with self.cv:
                    group["running"] -= 1
                    group["remaining"] -= 1
                    if result is None:
                        group["cancelled"] = True
                    else:
                        group["results"].append(result)
                    self.cv.notify_all()
        except BaseException as exc:
            self.fail(exc)
            self.stop()

    def _coordinate(self):
        try:
            from native_windows import plan
            from console_native import merge

            while True:
                with self.cv:
                    self.cv.wait_for(lambda: self.closed or self.pending is not None)
                    if self.closed:
                        return
                    epoch, job = self.pending
                    self.pending = None
                    tiles = plan(job["config"]["rectangle"])
                    g = dict(
                        job=job,
                        epoch=epoch,
                        tiles=deque(tiles),
                        remaining=len(tiles),
                        running=0,
                        results=[],
                        cancelled=False,
                    )
                    self.group = g
                    self.cv.notify_all()
                    self.cv.wait_for(lambda: self.closed or g["remaining"] == 0)
                    if self.closed:
                        return
                    valid = not g["cancelled"] and epoch == self.epoch
                if valid:
                    results = g["results"]
                    done = time.monotonic()
                    samples = [r[1] for r in results]
                    start = min(s["started"] for s in samples)
                    sample = dict(
                        time=done,
                        source_frame_id=job["frame"].source_frame_id,
                        tile_calls=len(samples),
                        queue_wait_ms=(start - job["frame"].capture_received_monotonic)
                        * 1000,
                        preprocessing_ms=sum(s["preprocessing_ms"] for s in samples),
                        rknn_ms=sum(s["rknn_ms"] for s in samples),
                        tile_wall_ms=(done - start) * 1000,
                        received_to_result_ms=(
                            done - job["frame"].capture_received_monotonic
                        )
                        * 1000,
                    )
                    self.publish(
                        job, (merge([o for rows, _ in results for o in rows]), sample)
                    )
                    with self.cv:
                        self.published += 1
                with self.cv:
                    self.group = None
                    self.cv.notify_all()
        except BaseException as exc:
            self.fail(exc)
            self.stop()

    def snapshot(self):
        with self.cv:
            g = self.group
            return dict(
                mode="two_cores_one_frame_latest_pending",
                capacity=2,
                pending=int(self.pending is not None),
                running=g["running"] if g else 0,
                awaiting_order=int(g is not None),
                completed_waiting=0,
                active_frame_tiles_remaining=g["remaining"] if g else 0,
                high_water=self.high_water,
                dropped=self.dropped,
                cancelled=self.cancelled,
                published=self.published,
                epoch=self.epoch,
            )

    def stop(self):
        with self.cv:
            self.closed = True
            self.pending = None
            self.cv.notify_all()

    def close(self):
        self.stop()
        for t in self.workers + [self.coordinator]:
            if t.ident is not None and t is not threading.current_thread():
                t.join(10)
            if t.is_alive():
                raise RuntimeError("Native tile worker did not stop: " + t.name)
