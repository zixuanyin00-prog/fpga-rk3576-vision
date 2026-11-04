from contextlib import contextmanager
from pathlib import Path
import os
import subprocess
import threading
import time
import traceback
from console_store import uid, now, write, ROOT


class Cancelled(Exception):
    pass


class Job:
    def __init__(self, flow, page, kind, serial):
        self.id = uid()
        self.flow = flow
        self.page = page
        self.kind = kind
        self.serial = serial
        self.cancel = threading.Event()
        self.process = None
        self.protected = False
        self.data = dict(
            id=self.id,
            flow=flow,
            page=page,
            kind=kind,
            state="queued",
            message="等待执行",
            started_at=now(),
        )
        self.lock = threading.RLock()

    def update(self, **values):
        with self.lock:
            self.data.update(values)

    def check(self):
        if self.cancel.is_set() and not self.protected:
            raise Cancelled("已让出操作权；原图、标注和已完成产物保留")

    def snapshot(self):
        with self.lock:
            result = dict(self.data)
        path = result.get("progress_file")
        if path:
            try:
                from console_store import read

                result["progress"] = read(path)
            except (OSError, ValueError):
                pass
        return result

    def run(self, args, cwd=None, timeout=3600):
        self.check()
        log = ROOT / "console_logs" / (self.id + ".log")
        log.parent.mkdir(exist_ok=True)
        with log.open("a", encoding="utf-8") as stream:
            proc = subprocess.Popen(
                args,
                cwd=cwd or ROOT,
                stdout=stream,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.process = proc
            deadline = time.monotonic() + timeout
            try:
                while proc.poll() is None:
                    self.check()
                    if time.monotonic() > deadline:
                        raise TimeoutError("任务超过时限；产物与日志已保留")
                    self.cancel.wait(0.2)
                if proc.returncode:
                    raise RuntimeError(
                        log.read_text(encoding="utf-8", errors="replace")[-2400:]
                    )
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(5)
                self.process = None


class Jobs:
    def __init__(self):
        self.lock = threading.Condition(threading.RLock())
        self.all = {}
        self.active = {}
        self.waiting = {}
        self.serial = 0

    def submit(self, flow, page, kind, action):
        with self.lock:
            self.serial += 1
            job = Job(flow, page, kind, self.serial)
            for old in self.all.values():
                if (
                    old.flow == flow
                    and old.data["state"] in ("queued", "running")
                    and not old.protected
                ):
                    old.cancel.set()
            self.all[job.id] = job
            self.lock.notify_all()

        def run():
            try:
                job.check()
                job.update(state="running")
                result = action(job)
                job.check()
                job.update(
                    state="complete",
                    message=(
                        job.data.get("message", "完成")
                        if job.data.get("capture_partial")
                        else "完成"
                    ),
                    result=result,
                )
            except Cancelled as exc:
                job.update(state="cancelled", message=str(exc))
            except Exception as exc:
                job.update(state="error", message=str(exc))
                path = ROOT / "console_logs" / (job.id + ".trace.txt")
                path.parent.mkdir(exist_ok=True)
                path.write_text(traceback.format_exc(), encoding="utf-8")
            finally:
                job.update(finished_at=now())
                write(ROOT / "console_jobs" / (job.id + ".json"), job.snapshot())
                with self.lock:
                    self.lock.notify_all()

        threading.Thread(target=run, daemon=True, name=kind + "-" + job.id[:6]).start()
        return job.snapshot()

    @contextmanager
    def resource(self, name, job):
        with self.lock:
            for older in self.waiting.get(name, {}).values():
                if older.serial < job.serial:
                    older.cancel.set()
            self.waiting.setdefault(name, {})[job.id] = job
            active = self.active.get(name)
            if active and active.serial < job.serial and not active.protected:
                active.cancel.set()
                active.update(message="新的主动操作接管，正在安全结束当前操作")
            try:
                while True:
                    job.check()
                    pending = [
                        j for j in self.waiting[name].values() if not j.cancel.is_set()
                    ]
                    if (
                        name not in self.active
                        and pending
                        and max(pending, key=lambda j: j.serial) is job
                    ):
                        self.active[name] = job
                        break
                    job.update(
                        message="等待"
                        + ("板端已提交事务结束" if name == "board" else "GPU释放")
                        + "；可继续其他页面操作"
                    )
                    self.lock.wait(0.25)
            except BaseException:
                self.waiting[name].pop(job.id, None)
                raise
            self.waiting[name].pop(job.id, None)
        try:
            job.check()
            yield
        finally:
            with self.lock:
                if self.active.get(name) is job:
                    self.active.pop(name, None)
                self.lock.notify_all()

    def state(self, flow=None):
        with self.lock:
            rows = [
                j.snapshot()
                for j in self.all.values()
                if flow is None or j.flow == flow
            ]
        return rows[-30:]

    def stop(self, flow, identity=None):
        with self.lock:
            for job in self.all.values():
                if job.flow == flow and (identity is None or job.id == identity):
                    if job.protected:
                        job.update(
                            message="模型切换事务正在提交，将在有界步骤结束后返回"
                        )
                    else:
                        job.cancel.set()
            self.lock.notify_all()
