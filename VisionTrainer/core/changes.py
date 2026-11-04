import threading
import uuid

_condition = threading.Condition()
_revision = 0
_instance = uuid.uuid4().hex


def publish():
    global _revision
    with _condition:
        _revision += 1
        _condition.notify_all()


def wait(after=-1, instance="", timeout=20):
    with _condition:
        if instance == _instance and after == _revision:
            _condition.wait_for(lambda: _revision != after, timeout=timeout)
        return dict(instance=_instance, revision=_revision)
