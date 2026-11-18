                                                                         

from collections import deque
from copy import deepcopy
import json
import os
from pathlib import Path
import time
from uuid import uuid4


IDENTITY_FIELDS = (
    "boot_id",
    "runtime_session_id",
    "firmware_sha256",
    "driver_sha256",
    "region_version",
    "origin",
)
TRANSACTION_TIMEOUT = 40
STATUS_TIMEOUT = 6


class CameraControls:
                                                                                

    def __init__(self, folder):
        self.folder = Path(folder)
        self.status = None
        self.message = ""
        self.last_ok = None
        self._requests = deque()
        self._completed = 0
        self._identity = None
        self._ticket = None
        self._receipt = None
        self._deadline = 0.0

    @property
    def pending(self):
        return bool(self._requests)

    def submit(self, *requests):
        if not requests or self.pending:
            return False
        if self.status is None:
            self.message = "相机状态未就绪"
            return False
        self._identity = {key: self.status["identity"][key] for key in IDENTITY_FIELDS}
        self._requests.extend(deepcopy(requests))
        self.last_ok = None
        self._completed = 0
        self._deadline = time.monotonic() + TRANSACTION_TIMEOUT
        self.message = "等待提交"
        return True

    def cancel_queued(self):
        if self.pending and self._ticket is None:
            self._finish("已取消未提交的操作")
            return True
        return False

    def _finish(self, message, *, ok=False):
        self._requests.clear()
        self._ticket = None
        self._receipt = None
        self.last_ok = ok
        if not ok and self._completed:
            message = f"已有 {self._completed} 项板端操作生效。{message}"
        self.message = message

    def poll(self):
        now = time.monotonic()
        try:
            status = json.loads((self.folder / "status.json").read_text(encoding="utf-8"))
            age = time.monotonic() - status["recorded_monotonic"]
                                                                     
            identity = status["identity"]
            self.status = status if -0.05 <= age < 5 and all(key in identity for key in IDENTITY_FIELDS) else None
        except (OSError, ValueError, KeyError, TypeError):
            self.status = None
        if not self.pending:
            return self.status

        if now >= self._deadline:
            if self._receipt is not None:
                self._finish("板端已确认，状态发布尚未同步")
            elif self._ticket is not None:
                self._finish("等待板端结果超时，请读回参数确认")
            else:
                self._finish("板端忙，等待提交超时")
            return self.status

        try:
            self._advance(now)
        except (OSError, ValueError) as error:
            self._finish(f"相机事务失败：{error}")
        return self.status

    def _advance(self, now):
        if self._ticket is not None:
            if self._receipt is None:
                try:
                    path = self.folder / self._ticket / "result.json"
                    result = json.loads(path.read_text(encoding="utf-8"))
                except FileNotFoundError:
                    return
                if result.get("id") != self._ticket:
                    raise ValueError("板端事务编号不一致")
                if not result.get("ok"):
                    message = result.get("error") or "板端未完成操作"
                    if result.get("partial_write_possible"):
                        message += "。请核对当前回读值"
                    self._finish(message)
                    return
                self._receipt = result
                self.message = "板端已确认，等待读回"
                self._deadline = now + STATUS_TIMEOUT
            if self.status is None:
                return
            published = self.status.get("last_result") or {}
            if published.get("id") != self._ticket or self.status.get("busy"):
                return
            self._completed += 1
            self._requests.popleft()
            self._ticket = None
            self._receipt = None
            if not self._requests:
                self._finish("已回读并生效", ok=True)
                return
            self._deadline = now + TRANSACTION_TIMEOUT

        if self.status is None:
            return
        current = self.status["identity"]
        if any(current[key] != value for key, value in self._identity.items()):
            self._finish("相机会话或区域已变化，请重新操作")
            return
        mailbox = self.folder / "request.json"
        if self.status.get("busy") or (self.folder / "processing.json").exists() or mailbox.exists():
            self.message = "等待板端上一笔操作完成"
            return

        ticket = "hdmi_" + uuid4().hex
        temporary = self.folder / (ticket + ".tmp")
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(dict(self._requests[0], id=ticket, identity=current), stream)
                                                                            
                                                                 
            try:
                os.link(temporary, mailbox)
            except FileExistsError:
                self.message = "等待板端上一笔操作完成"
                return
        finally:
            temporary.unlink(missing_ok=True)
        self._ticket = ticket
        self._deadline = now + TRANSACTION_TIMEOUT
        self.message = "已提交，等待板端生效"
