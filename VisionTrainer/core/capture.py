import hashlib
import json
import time
from . import store as db

SIZE = (512, 288)


def fps_point(s, remote):
    status = json.loads(s.open(remote + "/runtime_status.json").read())
    displays = {}
    for name in status["display_outputs"]:

        if not name or any(
            c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
            for c in name
        ):
            raise ValueError("无效显示端名称")
        displays[name] = json.loads(
            s.open(remote + "/display_" + name + ".json").read()
        )
    now = float(s.open("/proc/uptime").read(64).split()[0])
    fresh = all(
        -0.011 <= now - d["recorded_monotonic"] < 3
        for d in [status] + list(displays.values())
    )
    boot = s.open("/proc/sys/kernel/random/boot_id").read().decode().strip()
    return dict(
        runtime=status, displays=displays, board_now=now, fresh=fresh, boot_id=boot
    )


def fps_summary(before, after):
    result = dict(
        verified=False,
        displays={},
        scope="Unique source-ID counters and X server completions; not optical scanout or recognition accuracy",
    )
    if (
        not before["fresh"]
        or not after["fresh"]
        or before["boot_id"] != after["boot_id"]
        or before["runtime"]["pid"] != after["runtime"]["pid"]
        or not before["displays"]
        or before["displays"].keys() != after["displays"].keys()
    ):
        result["reason"] = "显示端、服务或启动已改变，或遥测过期"
        return result
    for name, last in after["displays"].items():
        first = before["displays"][name]
        span = last["recorded_monotonic"] - first["recorded_monotonic"]
        if span <= 0:
            result["reason"] = "显示遥测时间未前进"
            return result
        unique = last["unique_frames"] - first["unique_frames"]
        gaps = last["skipped_source_frames"] - first["skipped_source_frames"]
        redraw = last["presentation"]["submitted"] - first["presentation"]["submitted"]
        complete = (
            last["presentation"]["completed"] - first["presentation"]["completed"]
        )
        result["displays"][name] = dict(
            seconds=span,
            fresh_fps=unique / span,
            source_gaps=gaps,
            window_redraw_fps=redraw / span,
            completed_transfers_per_second=complete / span,
            passed=span >= 30
            and 59.8 <= unique / span <= 60.2
            and gaps == 0
            and redraw >= unique
            and 59.8 <= complete / span <= 60.2,
        )
    first, last = before["runtime"], after["runtime"]
    span = last["recorded_monotonic"] - first["recorded_monotonic"]
    if span <= 0:
        result["reason"] = "预览遥测时间未前进"
        return result
    result.update(
        preview_acquisition_fps=(
            last["preview"]["preview_frames"] - first["preview"]["preview_frames"]
        )
        / span,
        preview_source_gaps=last["preview"]["source_gaps"]
        - first["preview"]["source_gaps"],
        pid=last["pid"],
        roi_reported_rate_start=(first.get("inference") or {}).get(
            "roi_calls_per_second"
        ),
        roi_reported_rate_end=(last.get("inference") or {}).get("roi_calls_per_second"),
    )
    result["inference_rates"] = {}
    for name, value in [
        ("start", first.get("inference") or {}),
        ("end", last.get("inference") or {}),
    ]:
        enabled = (first if name == "start" else last).get(
            "inference_enabled", bool(value)
        )
        result["inference_rates"][name] = dict(
            enabled=enabled,
            coarse_per_second=value.get(
                "coarse_fps",
                value.get("coarse_calls_per_second", 0 if not value else None),
            ),
            fine_per_second=value.get(
                "fine_model_calls_per_second",
                value.get("fine_calls_per_second", 0 if not value else None),
            ),
            roi_per_second=value.get(
                "roi_supply_fps",
                value.get("roi_calls_per_second", 0 if not value else None),
            ),
            note="disabled=0; absent enabled counters unknown, never inferred from redraws",
        )
    result["verified"] = (
        all(d["passed"] for d in result["displays"].values())
        and 59.8 <= result["preview_acquisition_fps"] <= 60.2
        and result["preview_source_gaps"] == 0
    )
    if not result["verified"]:
        result["reason"] = "至少30秒且全部启用显示端达到60个新源帧/秒、零缺口才通过"
    return result


def origin(value):
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 2
        or any(type(v) is not int for v in value)
    ):
        raise ValueError("采样坐标必须为两个整数")
    if not (0 <= value[0] <= 1408 and 0 <= value[1] <= 792):
        raise ValueError("采样区域必须在1920×1080源画面内")
    return list(value)


def probe(s, remote, dest, point, kind, check_cancel):
    point = origin(point)
    request = dict(id="probe_" + db.uid(), kind=kind, origin=point)
    control = remote + "/training_control"
    dest.mkdir(parents=True, exist_ok=True)
    try:
        s.stat(control)
    except FileNotFoundError:
        raise ValueError("板端尚未安装原生试拍接口，请先安装配套接口")
    try:
        s.stat(control + "/request.json")
    except FileNotFoundError:
        pass
    else:
        raise ValueError("板端正在处理另一项试拍，请稍后重试")
    temp = control + "/" + request["id"] + ".tmp"
    with s.open(temp, "w") as f:
        f.write(json.dumps(request))
    s.rename(temp, control + "/request.json")
    folder = control + "/" + request["id"]
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        check_cancel()
        try:
            result = json.loads(s.open(folder + "/result.json").read())
            break
        except FileNotFoundError:
            time.sleep(0.2)
    else:
        raise ValueError("原生试拍超时，请检查视觉服务；未开始批量采集")
    if not result.get("ok"):
        raise ValueError("试拍失败：" + result.get("error", "未知错误"))
    if (
        result["id"] != request["id"]
        or result["region"]["origin"] != point
        or result["region"]["size"] != list(SIZE)
    ):
        raise ValueError("试拍区域元数据不匹配")
    if len(result.get("frames", [])) != 3:
        raise ValueError("试拍帧数量不匹配")
    seen = set()
    for i, frame in enumerate(result["frames"]):
        if (
            frame["file"] != f"native_{i}.rgb"
            or frame["origin"] != point
            or not frame.get("native_roi")
            or frame["source_frame_id"] in seen
        ):
            raise ValueError("试拍不是独立原生ROI帧")
        seen.add(frame["source_frame_id"])
    overview = result["overview"]
    if (
        overview["file"] != "overview.rgb"
        or overview["size"] != list(SIZE)
        or overview["source_size"] != [1920, 1080]
        or overview["native_roi"]
    ):
        raise ValueError("全景定位图元数据不匹配")
    for f in [overview] + result["frames"]:
        path = dest / f["file"]
        s.get(folder + "/" + f["file"], str(path))
        raw = path.read_bytes()
        if len(raw) != SIZE[0] * SIZE[1] * 3:
            raise ValueError("试拍图像尺寸不正确")
        f["sha256"] = hashlib.sha256(raw).hexdigest()
    result.update(created=db.now(), origin=point)
    db.write(dest / "result.json", result)
    return result
