from pathlib import Path
import hashlib
import json
import shlex
import sys
import time


def int8_artifact(folder, manifest):
    from console_store import read, sha

    name = "detector_int8.rknn"
    hashes = manifest["output_hashes"]
    if not hashes.get(name) or not (folder / name).is_file():
        raise ValueError(
            "现场新图测试固定使用INT8 RKNN；请先将所选训练版本转换为INT8，未开始采图"
        )
    for filename in (name, "config.json", "quantization_manifest.json"):
        path = folder / filename
        if not path.is_file() or sha(path.read_bytes()) != hashes.get(filename):
            raise ValueError(
                "INT8测试文件与训练记录不一致：" + filename + "；未开始采图"
            )
    conversion = read(folder / "quantization_manifest.json")
    if (
        not conversion.get("completed")
        or conversion["settings"].get("quantized_dtype") != "w8a8"
        or conversion["artifacts"].get(name, {}).get("sha256") != hashes[name]
        or conversion["source_hashes"].get("detector.onnx")
        != hashes.get("detector.onnx")
    ):
        raise ValueError("所选版本缺少匹配的W8A8量化记录；未开始采图")
    config = read(folder / "config.json")
    if config.get("input_transform") != "native_tiles_v1" or config["input_size"] != [
        512,
        288,
    ]:
        raise ValueError("INT8新图测试需要原生512×288窗口，禁止缩放或填充")
    return dict(
        backend="RKNN-INT8-NPU",
        precision="int8",
        model_file=name,
        model_sha256=hashes[name],
        config_sha256=hashes["config.json"],
    )


def run(board, folder, root, frames, selected, inference, dest, job):
    from console_store import safe, sha, write
    from console_model_deploy import snapshot_runtime

    dest.mkdir(parents=True, exist_ok=True)
    runtime = snapshot_runtime(board)
    write(dest / "runtime_provenance.json", runtime)
    remote = board.remote + "/console_int8_tests/" + job.id
    board.command("mkdir -p " + shlex.quote(remote))
    for name, digest in (
        (inference["model_file"], inference["model_sha256"]),
        ("config.json", inference["config_sha256"]),
    ):
        if sha((folder / name).read_bytes()) != digest:
            raise ValueError("采图后所选INT8文件发生变化；保留原图，未加载模型")
        job.check()
        board.s.put(str(folder / name), remote + "/" + name)
    board.s.put(str(Path(__file__)), remote + "/worker.py")
    inputs = []
    for index, frame in enumerate(frames):
        job.check()
        path = safe(root, frame["file"])
        raw = path.read_bytes()
        w, h = frame["size"]
        if len(raw) != w * h * 3 or sha(raw) != frame["sha256"]:
            raise ValueError("测试原生RGB尺寸或哈希改变：" + frame["id"])
        name = f"frame_{index:02d}.rgb"
        board.s.put(str(path), remote + "/" + name)
        inputs.append(
            dict(
                id=frame["id"],
                file=name,
                size=[w, h],
                sha256=frame["sha256"],
                source_frame_id=frame["source_frame_id"],
            )
        )
    environment = runtime["service"]["environment"]
    spec = dict(
        selected=selected,
        inference=inference,
        frames=inputs,
        runtime_paths=runtime["search_path"],
        engine_library=environment.get(
            "VISION_RKNN_C_LIBRARY", board.remote + "/libnative_engine.so"
        ),
    )
    write(dest / "job.json", spec)
    board.atomic(remote + "/job.json", spec)
    executable = runtime["command"][0]
    command = (
        "exec taskset -c 0-2 nice -n 15 ionice -c 3 "
        + shlex.quote(executable)
        + " -I "
        + shlex.quote(remote + "/worker.py")
        + " --board "
        + shlex.quote(remote + "/job.json")
    )
    channel = board.client.get_transport().open_session(timeout=10)
    channel.set_combine_stderr(True)
    channel.exec_command(command)
    finished = False
    log = bytearray()
    deadline = time.monotonic() + 600
    try:
        while not channel.exit_status_ready():
            job.check()
            if time.monotonic() > deadline:
                raise TimeoutError("INT8新图测试超过时限；原图保留")
            while channel.recv_ready():
                log.extend(channel.recv(65536))
            job.update(
                message="RK NPU运行所选INT8新图测试；独立低频任务，未切换实时模型"
            )
            time.sleep(0.5)
        while channel.recv_ready():
            log.extend(channel.recv(65536))
        code = channel.recv_exit_status()
        finished = True
        (dest / "npu.log").write_bytes(log)
        if code:
            raise RuntimeError(
                log.decode("utf8", errors="replace")[-2400:]
                or "NPU INT8测试失败；未回退PT"
            )
        result = json.loads(board.bytes(remote + "/predictions.json"))
        if result["selected"] != selected or result["inference"] != inference:
            raise ValueError("NPU返回的INT8模型身份与本次选择不一致")
        if [
            (r["frame"], r["source_frame_id"], r["sha256"]) for r in result["rows"]
        ] != [(f["id"], f["source_frame_id"], f["sha256"]) for f in frames]:
            raise ValueError("NPU返回结果与本次原生测试图不一致")
        write(dest / "predictions.json", result)
        return result
    finally:
        if not finished:
            with board.s.open(remote + "/cancel", "wb") as stream:
                stream.write(b"cancel")
        channel.close()


def board_main(jobfile):
    import os
    import numpy as np

    path = Path(jobfile)
    folder = path.parent
    spec = json.loads(path.read_text())
    sys.path[:0] = spec["runtime_paths"]
    from native_engine import NativeEngine
    from native_outputs import unpack
    from native_decode import decode, merge
    from native_windows import windows, accepts_tile_box

    inference = spec["inference"]
    for name, digest in (
        (inference["model_file"], inference["model_sha256"]),
        ("config.json", inference["config_sha256"]),
    ):
        if hashlib.sha256((folder / name).read_bytes()).hexdigest() != digest:
            raise ValueError("Uploaded INT8 artifact hash differs: " + name)
    config = json.loads((folder / "config.json").read_text())
    engine = NativeEngine(library=spec["engine_library"])
    rows = []
    next_call = 0.0
    try:
        engine.load_rknn(folder / inference["model_file"])
        engine.init_runtime(core_mask=NativeEngine.NPU_CORE_0)
        if tuple(engine.input_shape) != (1, 288, 512, 3):
            raise ValueError("INT8 model requires native RGB512x288")
        for frame in spec["frames"]:
            raw = (folder / frame["file"]).read_bytes()
            w, h = frame["size"]
            if (
                len(raw) != w * h * 3
                or hashlib.sha256(raw).hexdigest() != frame["sha256"]
            ):
                raise ValueError("Uploaded native RGB hash/geometry differs")
            image = np.frombuffer(raw, np.uint8).reshape(h, w, 3)
            found = []
            for x, y, tw, th in windows(w, h):
                while time.monotonic() < next_call:
                    if (folder / "cancel").exists():
                        raise RuntimeError("INT8 test cancelled")
                    time.sleep(min(0.1, max(0.0, next_call - time.monotonic())))
                if (folder / "cancel").exists():
                    raise RuntimeError("INT8 test cancelled")
                outputs = engine.inference(
                    inputs=[np.ascontiguousarray(image[y : y + th, x : x + tw])[None]],
                    data_format=["nhwc"],
                )
                next_call = time.monotonic() + 1.0
                p, logits = unpack(outputs, len(config["labels"]))
                for obj in decode(
                    (p, logits),
                    labels=config["labels"],
                    object_threshold=config["object_threshold"],
                    state_threshold=config["state_threshold"],
                ):
                    if accepts_tile_box(obj["bbox"], (x, y, tw, th), (0, 0, w, h)):
                        l, t, r, b = obj["bbox"]
                        found.append(dict(obj, bbox=[l + x, t + y, r + x, b + y]))
            rows.append(
                dict(
                    frame=frame["id"],
                    source_frame_id=frame["source_frame_id"],
                    sha256=frame["sha256"],
                    detections=merge(found),
                )
            )
        result = dict(
            rows=rows,
            backend=inference["backend"],
            inference=inference,
            selected=spec["selected"],
            pixel_policy="native_crop_only_v1",
            helper_detector_only=False,
            accuracy=None,
            accuracy_note="INT8 NPU单图结果，由用户复核；不含ID跟踪、计数和HDMI显示对齐",
            live_model_replaced=False,
            model_cpu_fallback=False,
            worker_pid=os.getpid(),
        )
        (folder / "predictions.json").write_text(
            json.dumps(result, ensure_ascii=False), encoding="utf8"
        )
    finally:
        engine.release()


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--board":
        raise SystemExit(
            "This worker is launched only by an explicitly selected Web INT8 test"
        )
    board_main(sys.argv[2])
