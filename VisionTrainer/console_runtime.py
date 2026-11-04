import ast


def patch_pipeline(source):
    tree = ast.parse(source)
    classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
    if "RectPipeline" not in classes or "Tracks" not in classes:
        raise ValueError("rect_pipeline.py: 缺少 RectPipeline 或 Tracks，未停止服务")
    cls = classes["RectPipeline"]
    lines = source.splitlines(keepends=True)
    methods = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
    missing = {"_infer", "_writer"} - methods.keys()
    if missing:
        raise ValueError(
            "rect_pipeline.py: RectPipeline 缺少 "
            + ",".join(sorted(missing))
            + "，未停止服务"
        )
    for name in sorted(
        ("_infer", "_writer"), key=lambda name: methods[name].lineno, reverse=True
    ):
        method = methods[name]
        replacement = (
            "    def _infer(self, core, job):\n        return console_native.infer(self, core, job)\n"
            if name == "_infer"
            else "    def _writer(self):\n        return console_native.write_failures(self)\n"
        )
        lines[method.lineno - 1 : method.end_lineno] = [replacement]
    source = "".join(lines)
    capture = "config=settings(req['values']);console_native.capture_rectangle(config,self.config)"
    if capture not in source:
        source = source.replace("config=settings(req['values'])", capture)

    source = source.replace(
        "from ordered_inference import OrderedInference",
        "from native_scheduler import FrameTiles as OrderedInference",
    )
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    tracks = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Tracks"
    )
    observers = [
        n for n in tracks.body if isinstance(n, ast.FunctionDef) and n.name == "observe"
    ]
    if len(observers) != 1:
        raise ValueError("rect_pipeline.py: 需要唯一的 Tracks.observe，未停止服务")
    observe = observers[0]
    lines[observe.lineno - 1 : observe.end_lineno] = [
        "    def observe(self,objects,source,now,hits,threshold):\n        return console_native.observe_tracks(self,objects,source,now,hits,threshold)\n"
    ]
    source = "".join(lines)
    replacements = [
        (
            "self.config.get('input_transform')!='stretch_bilinear'",
            "self.config.get('input_transform') not in ('stretch_bilinear','native_tiles_v1')",
            2,
        ),
        (
            "not in ('native_512x288','stretch_bilinear')",
            "not in ('native_512x288','stretch_bilinear','native_tiles_v1')",
            1,
        ),
        (
            "rect=value if operation=='capture' else config['rectangle']",
            "rect=value if operation=='capture' else console_native.capture_rectangle(config,self.config)",
            1,
        ),
        (
            "rect==config['rectangle']",
            "rect==console_native.capture_rectangle(config,self.config)",
            2,
        ),
    ]
    for before, after, count in replacements:
        if source.count(before) + source.count(after) != count:
            raise ValueError(
                "rect_pipeline.py: 当前原生窗口接口不匹配，未停止服务：" + before
            )
        source = source.replace(before, after)

    source = source.replace(
        "if now-self.last_time<.3 else []", "if now-self.last_time<2.0 else []"
    )
    source = source.replace(
        "('queue_wait_ms','preprocessing_ms','rknn_ms','received_to_result_ms')",
        "('queue_wait_ms','preprocessing_ms','input_copy_ms','decode_ms','rknn_ms','received_to_result_ms')",
    )
    imported = any(
        isinstance(n, ast.Import) and any(a.name == "console_native" for a in n.names)
        for n in tree.body
    )
    return source if imported else "import console_native\n" + source


def patch_view(source):

    tree = ast.parse(source)
    classes = [
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ScreenView"
    ]
    methods = [
        n
        for c in classes
        for n in c.body
        if isinstance(n, ast.FunctionDef) and n.name == "show"
    ]
    if len(methods) != 1:
        raise ValueError("native_view.py: 需要唯一的 ScreenView.show，未停止服务")
    show = methods[0]

    def same(node, expression):
        return ast.unparse(node) == ast.unparse(ast.parse(expression, mode="eval").body)

    metrics = [
        n
        for n in ast.walk(show)
        if isinstance(n, ast.Assign)
        and len(n.targets) == 1
        and same(n.targets[0], "self.ui_metrics")
    ]
    keys = [
        n
        for n in show.body
        if isinstance(n, ast.Assign)
        and len(n.targets) == 1
        and same(n.targets[0], "key")
    ]
    if len(metrics) != 1 or len(keys) != 1:
        raise ValueError(
            "native_view.py: ScreenView.show 缺少唯一的 ui_metrics 或 UI 缓存键，未停止服务"
        )
    metric = metrics[0]
    key = keys[0]
    call = metric.value
    blocks = [n for n in show.body if isinstance(n, ast.If) and metric in n.body]
    if (
        len(blocks) != 1
        or not isinstance(call, ast.Call)
        or not same(call.func, "dict")
        or len(call.args) != 1
        or not same(call.args[0], "self.measured")
        or not isinstance(key.value, ast.Tuple)
        or len(key.value.elts) < 4
        or not same(
            ast.Tuple(elts=key.value.elts[:4], ctx=ast.Load()),
            "(self.w,self.h,self.full,self.chart.serial)",
        )
    ):
        raise ValueError(
            "native_view.py: ui_metrics 不在已支持的异步 UI 缓存边界，未停止服务"
        )
    block = blocks[0]
    old_condition = "sampled or not self.ui_metrics"
    new_condition = old_condition + " or began>=getattr(self,'console_ui_due',0)"
    if not (same(block.test, old_condition) or same(block.test, new_condition)):
        raise ValueError("native_view.py: UI 更新条件未知，未停止服务")
    edits = []
    counting = [k for k in call.keywords if k.arg == "counting"]
    if counting and (
        len(counting) != 1 or not same(counting[0].value, "row.get('counting',{})")
    ):
        raise ValueError("native_view.py: 分类元数据来源未知，未停止服务")
    if not counting:
        call.keywords.append(
            ast.keyword(
                arg="counting",
                value=ast.parse("row.get('counting',{})", mode="eval").body,
            )
        )
    serial = "getattr(self,'console_ui_serial',0)"
    if not any(same(n, serial) for n in key.value.elts):
        key.value.elts.append(ast.parse(serial, mode="eval").body)
        edits.append((key.value, ast.unparse(key.value)))

    refresh = (
        ("self.console_ui_due", "began+.5"),
        ("self.console_ui_serial", serial + "+1"),
    )
    missing = []
    for target, value in refresh:
        assignments = [
            n
            for n in block.body
            if isinstance(n, ast.Assign)
            and len(n.targets) == 1
            and same(n.targets[0], target)
        ]
        if assignments and (
            len(assignments) != 1 or not same(assignments[0].value, value)
        ):
            raise ValueError(
                "native_view.py: 分类面板刷新状态未知：" + target + "，未停止服务"
            )
        if not assignments:
            missing.append(target + "=" + value)
    if not same(block.test, new_condition):
        edits.append((block.test, new_condition))
    if missing or not counting:

        statement = (
            "self.ui_metrics=" + ast.unparse(call)
            if not counting
            else ast.get_source_segment(source, metric)
        )
        edits.append(
            (
                metric,
                "\n".join(missing + [statement]).replace(
                    "\n", "\n" + " " * metric.col_offset
                ),
            )
        )

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and same(node.func, "draw_chart")
            and len(node.args) == 7
            and same(node.args[-1], "h-20")
        ):
            edits.append((node.args[-1], "h-270 if w>=1100 and h>=1000 else h-20"))
    raw = source.encode("utf-8")
    lines = raw.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    for node, replacement in sorted(
        edits, key=lambda e: (e[0].lineno, e[0].col_offset), reverse=True
    ):
        start = offsets[node.lineno - 1] + node.col_offset
        end = offsets[node.end_lineno - 1] + node.end_col_offset
        raw = raw[:start] + replacement.encode("utf-8") + raw[end:]
    source = raw.decode("utf-8")
    source = source.replace(
        "相机连续显示 · 图表按间隔采样 · 紫框为预测",
        "绿：同帧确认 · 黄：历史/待确认 · 紫：位置预测",
    )
    source = source.replace(
        "r.get('confirmed') and r.get('cached_fine')",
        "r.get('confirmed') and not r.get('display_history') and r.get('cached_fine')",
    )
    return source


def patch_preview(source):
    tree = ast.parse(source)
    assignments = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and len(n.targets) == 1
        and isinstance(n.targets[0], ast.Name)
        and n.targets[0].id == "display_row"
    ]
    if len(assignments) != 1:
        raise ValueError("shared_preview.py: 缺少唯一的 display_row，未停止服务")
    node = assignments[0]
    call = node.value
    if not (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "dict"
        and len(call.args) == 1
        and isinstance(call.args[0], ast.Name)
        and call.args[0].id == "row"
    ):
        raise ValueError(
            "shared_preview.py: display_row 不是已支持的行元数据接口，未停止服务"
        )
    fields = {k.arg: k.value for k in call.keywords}
    if "results" not in fields or ast.unparse(fields["results"]) != "aligned":
        raise ValueError("shared_preview.py: display_row 缺少 aligned 结果，未停止服务")
    observed = "row.get('results', [])"
    if (
        "classification_observations" in fields
        and ast.unparse(fields["classification_observations"]) != observed
    ):
        raise ValueError("shared_preview.py: 分类观测来源未知，未停止服务")
    if "classification_observations" in fields and "display_alignment_state" in fields:

        return source
    if "display_alignment_state" in fields:
        raise ValueError("shared_preview.py: 分类面板元数据不完整，未停止服务")
    replacement = """dict(row,results=aligned,classification_observations=row.get('results',[]),
                    display_alignment_state=dict(source_frame_id=int(source),
                        history_boxes=sum(bool(r.get('display_history')) for r in aligned),
                        predicted_boxes=sum(bool(r.get('display_prediction')) for r in aligned),
                        current_boxes=sum(not r.get('display_history') and not r.get('display_prediction') for r in aligned),
                        max_source_gap=max((r.get('prediction_frames',0) for r in aligned),default=0),
                        max_age_ms=round(max((r.get('display_observation_age_ms',0.) for r in aligned),default=0.),1)))"""
    if set(fields) - {"results", "classification_observations"}:
        raise ValueError("shared_preview.py: 未适配 display_row 含未知字段，未停止服务")
    raw = source.encode("utf-8")
    lines = raw.splitlines(keepends=True)
    start = sum(map(len, lines[: call.lineno - 1])) + call.col_offset
    end = sum(map(len, lines[: call.end_lineno - 1])) + call.end_col_offset
    source = (raw[:start] + replacement.encode("utf-8") + raw[end:]).decode("utf-8")
    return source.replace(
        "from display_alignment import DisplayAlignment",
        "from console_display import DisplayAlignment",
    )


def entry_source(root):
    return '''"""Explicit native-console deployment entry. Reuses the current display/DMA owner."""
from pathlib import Path
import importlib.util, sys
ROOT=Path(ROOT_LITERAL)
OVERLAY=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(OVERLAY))
spec=importlib.util.spec_from_file_location('rect_pipeline',OVERLAY/'rect_pipeline.py')
module=importlib.util.module_from_spec(spec)
sys.modules['rect_pipeline']=module
spec.loader.exec_module(module)
import console_native
console_native.install_display_titles(ROOT)
from run_live import main
if __name__=='__main__':
    main()
'''.replace(
        "ROOT_LITERAL", repr(root)
    )
