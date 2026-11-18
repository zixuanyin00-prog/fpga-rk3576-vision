                                                                       

from pathlib import Path
import threading
import time

from hdmi_controls import CameraControls, IDENTITY_FIELDS


class AdjustmentStopped(Exception):
    pass


class AutoAdjustment:
    def __init__(self, runtime):
        self.runtime = Path(runtime)
        self.message = ""
        self.last_ok = None
        self._stop = threading.Event()
        self._worker = None

    @property
    def pending(self):
        return self._worker is not None and self._worker.is_alive()

    def start(self, kind):
        if kind not in ("auto_exposure", "auto_adjust"):
            raise ValueError("Unknown camera adjustment")
        if self.pending:
            return False
        self.message = "正在自动曝光" if kind == "auto_exposure" else "正在校准白平衡"
        self.last_ok = None
        self._stop.clear()
        self._worker = threading.Thread(target=self._run, args=(kind,), daemon=True)
        self._worker.start()
        return True

    def cancel(self):
        if self.pending:
            self._stop.set()
            self.message = "正在停止，等待当前板端操作结束"

    def stop(self):
        self.cancel()

    def check(self):
        if self._stop.is_set():
            raise AdjustmentStopped

    def update(self, *, message=None, **_):
        if message and not self._stop.is_set():
            self.message = message

    def _run(self, kind):
        try:
                                                                         
                                                                                   
            from console_board import Board

            class LocalBoard(Board):
                def __init__(self, runtime):
                    self.remote = str(runtime)
                    self.controls = CameraControls(runtime / "camera_console")
                    status = self.controls.poll()
                    if not status:
                        raise RuntimeError("相机状态未就绪")
                    self.identity = {key: status["identity"][key] for key in IDENTITY_FIELDS}

                def bytes(self, path):
                    return Path(path).read_bytes()

                def check_identity(self, status):
                    if not status:
                        raise RuntimeError("相机状态未就绪")
                    if any(status["identity"][key] != value for key, value in self.identity.items()):
                        raise RuntimeError("相机会话或区域已变化，请重新操作")

                def fresh(self, name):
                    status = super().fresh(name)
                    if name == "camera_console/status.json":
                        self.check_identity(status)
                    return status

                def transaction(self, folder, body, job, timeout=40):
                    if folder != "camera_console":
                        raise ValueError("Only camera transactions are available")
                    job.check()
                    self.check_identity(self.controls.poll())
                    if not self.controls.submit(body):
                        raise RuntimeError(self.controls.message)
                    while self.controls.pending:
                        if job._stop.is_set() and self.controls.cancel_queued():
                            job.check()
                        self.controls.poll()
                        if self.controls.pending:
                            time.sleep(.15)
                    if not self.controls.last_ok:
                        raise RuntimeError(self.controls.message)
                    result = self.controls.status["last_result"]
                    job.check()
                    return result, str(self.controls.folder / result["id"])

                def wait_camera_result(self, ident):
                                                                            
                                            
                    return self.controls.status

            result = LocalBoard(self.runtime).camera(kind, {}, self)
            self.check()
            if kind == "auto_exposure":
                self.message = f'自动曝光完成 · {result["exposure_us"]:.1f} μs'
            else:
                self.message = result["white_balance"]["message"]
            self.last_ok = True
        except AdjustmentStopped:
            self.message = "调节已停止，请查看当前回读值"
            self.last_ok = False
        except Exception as error:
            self.message = f"自动调节失败：{error}"
            self.last_ok = False
