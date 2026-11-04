import inspect
import json


def _remote_probe(options):
    import ast
    import hashlib
    import importlib.machinery
    import json
    import os
    from pathlib import Path
    import re
    import shlex
    import subprocess
    import sys
    import time

    if hasattr(os, "sched_setaffinity"):
        os.sched_setaffinity(0, {0})
    pid = options.get("pid")
    entry_argument = options.get("entry")
    root_argument = options.get("root")
    environment = {}
    command = []
    if pid is not None:
        proc = Path("/proc") / str(int(pid))
        command = [
            part.decode()
            for part in (proc / "cmdline").read_bytes().split(b"\0")
            if part
        ]
        cwd = str((proc / "cwd").resolve())
        for part in (proc / "environ").read_bytes().split(b"\0"):
            if b"=" in part:
                key, value = part.split(b"=", 1)
                environment[key.decode()] = value.decode()
        choices = [part for part in command[1:] if part.endswith(".py")]
        if len(choices) != 1:
            raise ValueError(
                "Cannot identify one actual Python entry from /proc cmdline"
            )
        if (
            entry_argument
            and Path(entry_argument).resolve() != Path(choices[0]).resolve()
        ):
            raise ValueError("Expected runtime entry differs from /proc cmdline")
        entry_argument = choices[0]
    else:
        if not entry_argument:
            raise ValueError(
                "Provide an actual PID or an explicit entry for inspection"
            )
        cwd = str(Path(root_argument or Path(entry_argument).parent).resolve())
        command = [sys.executable, "-u", str(Path(entry_argument).resolve())]
    entry = Path(entry_argument).resolve()
    raw_entry = entry.read_bytes()
    tree = ast.parse(raw_entry.decode("utf-8-sig"), filename=str(entry))
    overlay = entry.parent
    constants = {"OVERLAY": str(overlay)}
    unsupported = []
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            value = node.value
            if (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == "Path"
                and len(value.args) == 1
                and isinstance(value.args[0], ast.Constant)
                and isinstance(value.args[0].value, str)
            ):
                constants[node.targets[0].id] = value.args[0].value
            elif node.targets[0].id in ("ROOT", "OVERLAY"):
                if not (
                    node.targets[0].id == "OVERLAY"
                    and ast.unparse(value) == "Path(__file__).resolve().parent"
                ):
                    unsupported.append(ast.unparse(node))
    root = Path(root_argument or constants.get("ROOT", cwd)).resolve()

    search_paths = [str(overlay)] + [
        path for path in sys.path if path and path != str(overlay)
    ]
    declared_paths = []
    for node in tree.body:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if not (
            isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Attribute)
            and isinstance(call.func.value.value, ast.Name)
            and call.func.value.value.id == "sys"
            and call.func.value.attr == "path"
        ):
            continue
        if (
            call.func.attr != "insert"
            or len(call.args) != 2
            or not isinstance(call.args[0], ast.Constant)
        ):
            unsupported.append(ast.unparse(call))
            continue
        value = call.args[1]
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "str"
            and len(value.args) == 1
        ):
            value = value.args[0]
        if isinstance(value, ast.Name) and value.id in constants:
            path = (
                str(root)
                if value.id == "ROOT"
                else str(Path(constants[value.id]).resolve())
            )
        elif isinstance(value, ast.Constant) and isinstance(value.value, str):
            path = str(Path(value.value).resolve())
        else:
            unsupported.append(ast.unparse(call))
            continue
        search_paths.insert(int(call.args[0].value), path)
        declared_paths.append(path)

    search_paths = list(dict.fromkeys(search_paths))
    if str(root) not in search_paths:
        search_paths.append(str(root))
    scopes = (overlay, root)

    def file_record(path):
        path = Path(path)
        raw = path.read_bytes()
        return dict(
            path=str(path),
            realpath=str(path.resolve()),
            size=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(),
        )

    def owned(path):
        return any(path == base or base in path.parents for base in scopes)

    modules = {"__entry__": file_record(entry)}
    pending = {
        "rect_pipeline",
        "run_live",
        "native_view",
        "shared_preview",
        "console_native",
        "pose_chart",
        "apple_glass",
        "continuous_transport",
        "capture_scheduling",
        "capture_realtime",
        "native_tracking",
        "native_count_store",
        "motion_wire",
        "global_motion",
        "motion_display_alignment",
        "display_trace_async",
        "object_outcomes",
        "outcome_process_store",
        "outcome_writer_process",
        "evidence_process_bridge",
        "image_capture_process",
        "sequence_capture",
        "full_roi_runtime",
        "native_engine",
        "native_outputs",
        "native_decode",
        "selected_model_gate",
        "pipeline_control_io",
        "async_status_storage",
        "coordinator_environment",
        "camera_console_bridge",
        "auto_isp",
    }
    pending.update(path.stem for path in overlay.glob("*.py"))
    visited = set()
    unresolved = []
    external = []
    while pending:
        name = pending.pop().split(".")[0]
        if name in visited:
            continue
        visited.add(name)
        spec = importlib.machinery.PathFinder.find_spec(name, search_paths)
        if spec is None or not spec.origin or spec.origin in ("built-in", "frozen"):
            if name not in sys.builtin_module_names:
                unresolved.append(name)
            continue
        path = Path(spec.origin)
        if not owned(path):
            external.append(dict(name=name, path=str(path)))
            continue
        modules[name] = file_record(path)
        if path.suffix != ".py":
            continue
        source = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(source):
            if isinstance(node, ast.Import):
                pending.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                pending.add(node.module.split(".")[0])
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "with_name"
                and len(node.args) == 1
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                sibling = path.with_name(node.args[0].value)
                if sibling.suffix in (".py", ".so") and sibling.is_file():
                    modules["@asset:" + str(sibling)] = file_record(sibling)
    for directory in scopes:
        for path in directory.glob("*.so"):
            if path.is_file():
                modules["@asset:" + str(path)] = file_record(path)
    config_files = {}
    config_candidates = [
        root / name
        for name in (
            "demo_config.json",
            "image_controls.json",
            "camera_environment_identity.json",
            "native_lk_thread_plan.json",
            "startup_sensor.json",
            "startup_enhancement.json",
        )
    ]
    config_candidates.append(overlay / "saved_chart_settings.json")
    for path in config_candidates:
        if path.is_file():
            record = file_record(path)
            record["json"] = json.loads(path.read_text(encoding="utf-8-sig"))
            config_files[str(path)] = record
    state_files = {}
    for path in (
        root / "counter_state.json",
        overlay / "counter_restore.json",
        root / "runtime_status.json",
        root / "rect_control/status.json",
        root / "camera_console/status.json",
    ):
        if path.is_file():
            record = file_record(path)
            record["json"] = json.loads(path.read_text(encoding="utf-8-sig"))
            state_files[str(path)] = record
    model_root = root / "model"
    model_files = {}
    for name in ("config.json", "detector_fp16.rknn"):
        path = model_root / name
        if path.is_file():
            model_files[name] = file_record(path)
    ticket_path = environment.get("VISION_MODEL_SELECTION_TICKET")
    ticket = None
    if ticket_path and Path(ticket_path).is_file():
        ticket = json.loads(Path(ticket_path).read_text(encoding="utf-8-sig"))
        for name, key in (
            ("config.json", "fine_config_path"),
            ("detector_fp16.rknn", "fine_model_path"),
        ):
            if ticket.get(key) and Path(ticket[key]).is_file():
                model_files[name] = file_record(ticket[key])
    service = dict(
        exec_start=list(command),
        working_directory=cwd,
        environment=environment,
        unit_sha256=None,
        exec_start_definition=None,
    )
    if pid is not None:
        cat = subprocess.run(
            ["systemctl", "cat", "fpga-vision-v3.service"],
            capture_output=True,
            check=True,
        ).stdout
        service["unit_sha256"] = hashlib.sha256(cat).hexdigest()
        raw_exec = subprocess.run(
            [
                "systemctl",
                "show",
                "fpga-vision-v3.service",
                "-p",
                "ExecStart",
                "--value",
            ],
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()
        match = re.search(r"argv\[\]=(.*?);", raw_exec)
        service["exec_start_definition"] = match.group(1).strip() if match else raw_exec
        configured_environment = subprocess.run(
            [
                "systemctl",
                "show",
                "fpga-vision-v3.service",
                "-p",
                "Environment",
                "--value",
            ],
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()
        service["environment"] = dict(
            item.split("=", 1)
            for item in shlex.split(configured_environment)
            if "=" in item
        )
        service["working_directory"] = subprocess.run(
            [
                "systemctl",
                "show",
                "fpga-vision-v3.service",
                "-p",
                "WorkingDirectory",
                "--value",
            ],
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()
    return dict(
        schema="vision-runtime-provenance-v1",
        captured_monotonic=time.monotonic(),
        pid=pid,
        resolution_method="proc_entry_and_declared_import_search_path_static_no_application_import",
        runtime_sys_modules_observed=False,
        command=command,
        cwd=cwd,
        entry=str(entry),
        entry_sha256=modules["__entry__"]["sha256"],
        root=str(root),
        overlay=str(overlay),
        search_path=search_paths,
        declared_paths=declared_paths,
        unsupported_path_mutations=unsupported,
        modules=modules,
        module_paths={name: record["path"] for name, record in modules.items()},
        unresolved_imports=sorted(unresolved),
        external_modules=sorted(external, key=lambda row: row["name"]),
        config_files=config_files,
        state_files=state_files,
        service=service,
        model=dict(
            root=str(model_root),
            realpath=str(model_root.resolve()),
            link_path=str(model_root),
            is_symlink=model_root.is_symlink(),
            target=str(model_root.resolve()),
            files=model_files,
            weights_path=model_files.get("detector_fp16.rknn", {}).get("path"),
            config_path=model_files.get("config.json", {}).get("path"),
            sha256=model_files.get("detector_fp16.rknn", {}).get("sha256"),
            config_sha256=model_files.get("config.json", {}).get("sha256"),
            selection_ticket_path=ticket_path,
            selection=ticket,
        ),
    )


def probe_script(pid=None, entry=None, root=None):

    options = dict(pid=pid, entry=entry, root=root)
    return (
        "import json\n"
        + inspect.getsource(_remote_probe)
        + "\nprint(json.dumps(_remote_probe("
        + repr(options)
        + "), ensure_ascii=False))\n"
    )
