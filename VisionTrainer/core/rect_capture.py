import hashlib
import io
import json
import time
import uuid
from PIL import Image
from . import store as db
from .rectangles import rectangle


def read(s, path):
    return json.loads(s.open(path).read())


def status(s, remote):
    value = read(s, remote + "/rect_control/status.json")
    uptime = float(s.open("/proc/uptime").read().split()[0])
    if (
        value.get("protocol") != "native-rectangle-v1"
        or not 0 <= uptime - value["recorded_monotonic"] < 3
    ):
        raise ValueError("自由矩形接口未安装或状态已过期")
    return value


def request(service, s, remote, payload, dest, allow_partial=False):
    state = status(s, remote)
    ident = uuid.uuid4().hex
    control = remote + "/rect_control"
    for name in ("request.json", "processing.json"):
        try:
            s.stat(control + "/" + name)
        except FileNotFoundError:
            pass
        else:
            raise ValueError("已有矩形操作正在执行，请稍后重试")
    body = dict(
        payload,
        id=ident,
        expected_version=payload.get("expected_version", state["version"]),
    )
    temp = control + "/" + ident + ".tmp"
    with s.open(temp, "w") as f:
        f.write(json.dumps(body))
    s.rename(temp, control + "/request.json")
    deadline = time.monotonic() + 100
    while time.monotonic() < deadline:
        service.check_cancel()
        try:
            result = read(s, control + "/" + ident + "/result.json")
            break
        except FileNotFoundError:
            time.sleep(0.2)
    else:
        raise ValueError("矩形接口超时；未重复提交")
    partial = (
        allow_partial
        and payload["operation"] == "capture"
        and result.get("terminal_status") == "exhausted"
        and result.get("protocol") == "native-rectangle-v1"
        and 0 < len(result.get("frames", [])) < payload["frames"]
    )
    if result.get("id") != ident or not (result.get("ok") or partial):
        raise ValueError(result.get("error", "矩形操作失败"))
    if dest is not None:
        dest.mkdir(parents=True, exist_ok=True)
        if payload["operation"] == "capture":
            rect = rectangle(payload["rectangle"])
            seen = set()
            if (
                result.get("protocol") != "native-rectangle-v1"
                or result.get("rectangle") != rect
                or (not partial and len(result["frames"]) != payload["frames"])
            ):
                raise ValueError("采集返回区域/数量不符")
            for i, frame in enumerate(result["frames"]):
                if (
                    frame["file"] != f"native_{i}.rgb"
                    or frame["rectangle"] != rect
                    or frame["source_frame_id"] in seen
                ):
                    raise ValueError("原始帧元数据不匹配")
                seen.add(frame["source_frame_id"])
                s.get(
                    control + "/" + ident + "/" + frame["file"],
                    str(dest / frame["file"]),
                )
                raw = (dest / frame["file"]).read_bytes()
                if (
                    len(raw) != rect[2] * rect[3] * 3
                    or hashlib.sha256(raw).hexdigest() != frame["sha256"]
                ):
                    raise ValueError("原始矩形图像校验失败")
            s.get(control + "/" + ident + "/overview.rgb", str(dest / "overview.rgb"))
            if len((dest / "overview.rgb").read_bytes()) != 512 * 288 * 3:
                raise ValueError("全景图像尺寸错误")
        db.write(dest / "result.json", result)
    return result


def probe(service, id, rect):
    p = db.load(id)
    rect = rectangle(rect)
    root = db.project_path(id)
    dest = root / "probes" / db.uid()
    c = service.connect()
    s = c.open_sftp()
    remote = service.settings["remote"].rstrip("/")
    try:
        r = request(
            service,
            s,
            remote,
            dict(operation="capture", rectangle=rect, frames=3),
            dest,
        )
        r.update(
            origin=rect[:2],
            size=rect[2:],
            created=db.now(),
            folder=str(dest.relative_to(root)),
            host=service.settings["host"],
            remote=remote,
            boot_id=r["identity"]["boot_id"],
            rectangle=rect,
        )
        with service.lock:
            p = db.load(id)
            p["probe"] = r
            p.pop("calibration", None)
            p.get("environment", {}).pop("approved", None)
            db.save(p)
    finally:
        s.close()
        c.close()


def capture(service, id, role, batch=None):
    from .capture import fps_point, fps_summary
    from .pc_testing import same_parameters

    p = db.load(id)
    rect = rectangle(p["calibration"]["rectangle"])
    root = db.project_path(id)
    profile = p.get("environment", {}).get("approved")
    if not profile:
        raise ValueError("请先固定当前成像配置")
    prior = [f for f in p["frames"] if f.get("batch") == batch] if batch else []
    if any(
        not f.get("excluded")
        and f.get("quality_review") != "reject"
        and not (
            f.get("reviewed")
            and f.get("quality_review") == "pass"
            and f.get("all_objects_reviewed")
        )
        for f in prior
    ):
        raise ValueError("请先完成已有图的质量和全部目标确认，再补采")
    if prior and any(
        f.get("calibration", {}).get("rectangle") != rect
        or f.get("capture_session") != p["calibration"]["probe_id"]
        for f in prior
    ):
        raise ValueError("补采选区或试拍已改变")
    from .batch15 import accepted_frames

    target = (
        (15 - len(accepted_frames(prior, p["schema"]["labels"])))
        if batch
        else (10 if role == "test" else 15)
    )
    if not 1 <= target <= 15:
        raise ValueError("无需补采")
    batch = batch or db.uid()
    dest = root / "captures" / batch / db.uid()
    c = service.connect()
    s = c.open_sftp()
    remote = service.settings["remote"].rstrip("/")
    try:
        before = fps_point(s, remote)
        board = read(s, remote + "/camera_console/status.json")
        from .environment15 import approved_profile

        runtime = read(s, remote + "/runtime_status.json")
        uptime = float(s.open("/proc/uptime").read().split()[0])
        approved_profile(board, runtime, uptime, board["identity"]["origin"])
        if (
            same_parameters(profile, dict(board=board))
            or board["identity"]["boot_id"] != p["calibration"]["boot_id"]
        ):
            raise ValueError("相机配置或启动身份改变，请重新确认")
        r = request(
            service,
            s,
            remote,
            dict(
                operation="capture",
                rectangle=rect,
                frames=target,
                hz=service.settings["hz"],
            ),
            dest,
            allow_partial=True,
        )
        if r["identity"] != board["identity"]:
            raise ValueError("采集前后相机身份不一致")
        after = fps_point(s, remote)
        fps = fps_summary(before, after)
        items = []
        for i, meta in enumerate(r["frames"]):
            if any(
                f.get("source_frame_id") == meta["source_frame_id"]
                or f.get("original_sha256") == meta["sha256"]
                for f in prior
            ):
                raise ValueError("补采含重复原图")
            raw = (dest / meta["file"]).read_bytes()
            model = (
                Image.frombytes("RGB", tuple(rect[2:]), raw)
                .resize((512, 288), Image.Resampling.BILINEAR)
                .tobytes()
            )
            name = f"input_{i}.rgb"
            (dest / name).write_bytes(model)
            items.append(
                dict(
                    id=db.uid(),
                    batch=batch,
                    source_group=service.settings["host"] + "/" + batch,
                    role=role,
                    file=str((dest / name).relative_to(root)),
                    sha256=hashlib.sha256(model).hexdigest(),
                    original_file=str((dest / meta["file"]).relative_to(root)),
                    original_sha256=meta["sha256"],
                    original_size=rect[2:],
                    input_transform=dict(
                        kind="stretch_bilinear",
                        source_size=rect[2:],
                        model_size=[512, 288],
                    ),
                    source_frame_id=meta["source_frame_id"],
                    origin=rect[:2],
                    native_roi=True,
                    model_input_is_native=False,
                    boot_id=r["identity"]["boot_id"],
                    capture_session=p["calibration"]["probe_id"],
                    imaging_identity=profile["profile_digest"],
                    scene_spec=dict(rectangle=rect, identity=r["identity"]),
                    calibration=p["calibration"],
                    quality_review="pending",
                    all_objects_reviewed=False,
                    objects=[],
                    reviewed=False,
                )
            )
        with service.lock:
            p = db.load(id)
            db.add_frames(p, items)
            p["last_capture"] = dict(
                batch=batch,
                created=db.now(),
                frames=len(items),
                source_frames=len(items),
                display=fps,
                requested_frames=target,
                complete=bool(r["ok"]),
            )
            p.setdefault("capture_profiles", {})[batch] = profile
            db.save(p)
            if not r["ok"]:
                service.job["capture_warning"] = (
                    f"已保留{len(items)}/{target}张，请复核后按缺额补采"
                )
        db.write(
            dest / "capture_evidence.json",
            dict(
                rectangle=rect,
                display=fps,
                originals_preserved=True,
                input_transform="stretch_bilinear",
            ),
        )
    finally:
        s.close()
        c.close()
    if (
        role == "train"
        and service.box_selection
        and service.box_selection.get("project") == id
        and service.box_selection.get("scene", {}).get("batch") == batch
    ):
        service.suggest_action(id, items, dest / "boxes")
    return items
