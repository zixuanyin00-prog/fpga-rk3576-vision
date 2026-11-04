from collections import deque
from functools import lru_cache
import colorsys
import hashlib
import math
from passage_stats import PassageWindow, MAX_WINDOW_SECONDS

WINDOW_SECONDS = 5.0
PAGE_SIZE = 10
GENERAL = [
    ("total", "总数"),
    ("unrecognized", "未识别"),
    ("confirmed_total", "已确认"),
    ("current_present", "在场"),
    ("pending", "待确认"),
    ("unconfirmed", "无法确认"),
    ("identity_lost_unconfirmed", "身份丢失"),
    ("interrupted_unconfirmed", "观察中断"),
    ("legacy_unresolved", "历史未明确"),
]


def color(key):
    hue = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return tuple(round(x * 255) for x in colorsys.hsv_to_rgb(hue, 0.55, 0.98))


def _pie_signature(rgb):

    linear = tuple(
        v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
        for v in (c / 255 for c in rgb)
    )
    transforms = (
        None,
        ((0.152, 1.053, -0.205), (0.115, 0.786, 0.099), (-0.004, -0.048, 1.052)),
        ((0.367, 0.861, -0.228), (0.280, 0.673, 0.047), (-0.012, 0.043, 0.969)),
        ((1.256, -0.077, -0.179), (-0.078, 0.931, 0.148), (0.005, 0.691, 0.304)),
    )
    result = []
    for matrix in transforms:
        values = (
            linear
            if matrix is None
            else tuple(
                max(0.0, min(1.0, sum(a * b for a, b in zip(row, linear))))
                for row in matrix
            )
        )
        luminance = sum(a * b for a, b in zip(values, (0.2126, 0.7152, 0.0722)))
        result.append(tuple(math.sqrt(v) for v in values) + (math.sqrt(luminance),))
    return tuple(result)


def _pie_distance(a, b):
    return min(
        sum((x - y) ** 2 for x, y in zip(left[:3], right[:3]))
        + 3 * (left[3] - right[3]) ** 2
        for left, right in zip(a, b)
    )


@lru_cache(maxsize=16)
def pie_colors(keys):

    if not keys:
        return {}
    neutral = tuple([round(0.72 * 255)] * 3)
    reserved = _pie_signature(neutral)
    chosen = [reserved]
    palette = {}

    hues = list(range(10, 36, 3)) + list(range(205, 256, 3)) + list(range(285, 336, 3))
    candidates = []
    for hue in hues:
        for light in (0.34, 0.43, 0.52, 0.61, 0.70, 0.79):
            for saturation in (0.58, 0.82):
                rgb = tuple(
                    round(c * 255)
                    for c in colorsys.hls_to_rgb(hue / 360, light, saturation)
                )
                signature = _pie_signature(rgb)
                if signature[0][3] ** 2 >= 0.14:
                    candidates.append((rgb, signature))
    distances = [_pie_distance(signature, reserved) for _, signature in candidates]
    available = set(range(len(candidates)))
    for key in keys:
        if key == "stat:unrecognized":
            palette[key] = neutral
            continue
        if not available:
            available = set(range(len(candidates)))
        index = max(
            available,
            key=lambda i: (
                distances[i] + 0.25 * _pie_distance(candidates[i][1], chosen[-1]),
                -i,
            ),
        )
        rgb, signature = candidates[index]
        palette[key] = rgb
        chosen.append(signature)
        available.remove(index)
        for i in available:
            distances[i] = min(distances[i], _pie_distance(candidates[i][1], signature))
    return palette


def number(value):
    return (
        float(value)
        if isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
        else None
    )


def normalize_counting(counting):
    result = dict(counting)
    counts = counting.get("class_counts", {})
    if isinstance(counts, list):
        result["classes"] = [
            dict(key=c["label"], title=c.get("title", c["label"])) for c in counts
        ]
        result["class_counts"] = {c["label"]: c.get("count") for c in counts}
    else:
        result["class_counts"] = dict(counts)
    observed = number(result.get("observed_total"))
    confirmed = number(result.get("confirmed_total", result.get("confirmed_abc_total")))
    result["confirmed_total"] = confirmed
    unknown = number(result.get("unrecognized"))

    if unknown is None and result.get("counting_semantics_version") == 2:
        result["unrecognized"] = number(result.get("final_unconfirmed", 0)) + number(
            result.get("legacy_unresolved", 0)
        )
    elif (
        unknown is None
        and observed is not None
        and confirmed is not None
        and observed >= confirmed
    ):
        result["unrecognized"] = observed - confirmed

    result["legacy_total"] = counting.get("total")
    if observed is not None:
        result["total"] = observed
    return result


def fields_for(counting):
    general = [
        dict(key="stat:" + key, title=title, color=color("stat:" + key))
        for key, title in GENERAL
    ]
    classes = [
        dict(
            key="class:" + c["key"], title=c["title"], color=color("class:" + c["key"])
        )
        for c in counting.get("classes", [])
    ]
    return (
        general[:1]
        + classes
        + general[1:]
        + [dict(key="rate:throughput", title="通过速度", color=(255, 172, 72))]
    )


class ChartState:
    def __init__(self, interval=0.5, denominator_mode="observed_total"):
        if denominator_mode not in ("observed_total", "current_present"):
            raise ValueError("Unsupported explicit percentage denominator")
        self.interval = max(0.5, min(2.5, float(interval)))
        self.denominator_mode = denominator_mode
        self.history = deque(maxlen=64)
        self.fields = fields_for({})
        self.enabled = {"stat:unrecognized"}
        self.seen_fields = set()
        self.session = None
        self.schema = None
        self.next_sample = 0.0
        self.serial = 0
        self.page = 0
        self.editing = False
        self.edit_text = ""
        self.error = ""
        self.latest = {}
        self.sampled_at = None
        self.denominator = None
        self.data_note = "等待已观测总数"
        self.mode = "line"
        self.pie_seconds = 5.0
        self.windows = PassageWindow()
        self.rate_history = deque(maxlen=64)
        self.rate = None
        self.pie = {}
        self.rate_summary = {}
        self.awaiting_reset = False
        self.reset_requests = 0
        self.edit_target = "interval"
        self.enabled.add("rate:throughput")

    def _clear(self, now):
        self.history.clear()
        self.rate_history.clear()
        self.windows = PassageWindow()
        self.rate = None
        self.pie = {}
        self.rate_summary = {}
        self.next_sample = float(now)
        self.sampled_at = float(now)
        self.latest = {f["key"]: 0.0 for f in self.fields}

    def update(self, counting, now):
        counting = normalize_counting(counting)
        schema = tuple((c["key"], c["title"]) for c in counting.get("classes", []))
        session = counting.get("session_id")
        if self.awaiting_reset and session == self.session:
            return False
        if schema != self.schema or session != self.session:
            self.awaiting_reset = False
            self.fields = fields_for(counting)
            self.schema = schema
            self.session = session
            keys = {f["key"] for f in self.fields}
            self.enabled.intersection_update(keys)
            self.enabled.update(
                f["key"]
                for f in self.fields
                if f["key"].startswith("class:") and f["key"] not in self.seen_fields
            )
            self.enabled.update(("stat:unrecognized", "rate:throughput"))
            self.seen_fields.update(keys)
            self._clear(now)
            self.page = 0
            self.serial += 1

        self.windows.ingest(counting.get("passage_stream"), float(now))
        if now < self.next_sample:
            return False
        values = {"stat:" + key: number(counting.get(key)) for key, _ in GENERAL}
        values.update(
            {
                "class:" + key: number(value)
                for key, value in counting["class_counts"].items()
            }
        )
        self.rate_summary = self.windows.summary(
            WINDOW_SECONDS, now, [c["key"] for c in counting.get("classes", [])]
        )
        self.rate = self.rate_summary["rate"] if self.rate_summary["complete"] else None
        values["rate:throughput"] = self.rate

        values["stat:confirmed_abc_total"] = values.get("stat:confirmed_total")
        self.latest = values
        if self.denominator_mode == "observed_total":
            numerator = values
            denominator = number(counting.get("observed_total"))
        else:

            current = counting.get("current_class_counts", {})
            if isinstance(current, list):
                current = {v["label"]: v.get("count") for v in current}
            numerator = {
                "class:" + key: number(value) for key, value in current.items()
            }
            numerator["stat:unrecognized"] = number(
                counting.get("current_unrecognized")
            )
            denominator = number(counting.get("current_present"))
        self.denominator = denominator
        if denominator is None:
            self.data_note = (
                "总数口径待确认"
                if self.denominator_mode == "observed_total"
                else "等待当前在场总数"
            )
        elif denominator == 0:
            self.data_note = (
                "暂无已观测对象"
                if self.denominator_mode == "observed_total"
                else "当前没有在场对象"
            )
        else:
            self.data_note = ""
        percentages = {
            key: (
                100 * value / denominator
                if denominator and value is not None and value <= denominator
                else None
            )
            for key, value in numerator.items()
            if key != "rate:throughput"
        }
        percentages["rate:throughput"] = self.rate
        self.history.append((float(now), percentages))
        self.rate_history.append((float(now), self.rate))

        while self.history and self.history[0][0] < now - WINDOW_SECONDS:
            self.history.popleft()
        while self.rate_history and self.rate_history[0][0] < now - WINDOW_SECONDS:
            self.rate_history.popleft()
        summary = self.windows.summary(
            self.pie_seconds, now, [c["key"] for c in counting.get("classes", [])]
        )
        sectors = [
            dict(
                key="class:" + c["key"],
                title=c["title"],
                count=summary["class_counts"][c["key"]],
                color=color("class:" + c["key"]),
            )
            for c in counting.get("classes", [])
        ]
        modern = summary.get("semantics_version", 1) >= 2
        sectors.append(
            dict(
                key="stat:unrecognized",
                title="最终未确认" if modern else "未识别",
                count=(
                    summary["final_unconfirmed"] if modern else summary["unrecognized"]
                ),
                color=color("stat:unrecognized"),
            )
        )
        if modern:
            for key, title, field in [
                ("pending", "待确认", "pending"),
                ("identity_lost_unconfirmed", "身份丢失", "identity_lost"),
                ("interrupted_unconfirmed", "观察中断", "observation_interrupted"),
                ("legacy_unresolved", "历史未明确", "legacy_unresolved"),
            ]:
                if summary[field]:
                    sectors.append(
                        dict(
                            key="stat:" + key,
                            title=title,
                            count=summary[field],
                            color=color("stat:" + key),
                        )
                    )
        for sector in sectors:
            sector["percentage"] = (
                100 * sector["count"] / summary["total"] if summary["total"] else 0.0
            )
        self.pie = dict(summary, sectors=sectors)
        self.sampled_at = float(now)
        self.next_sample = now + self.interval
        self.serial += 1
        return True

    def set_interval(self, value, now):
        try:
            value = float(value)
            if not math.isfinite(value) or not 0.5 <= value <= 60.0:
                raise ValueError()
        except (ValueError, TypeError):
            self.error = "更新间隔请输入 0.5～60 秒"
            self.serial += 1
            return False
        self.interval = value
        self.next_sample = now + value
        self.editing = False
        self.error = ""
        self.serial += 1
        return True

    def set_pie_seconds(self, value, now):
        try:
            value = float(value)
            if not math.isfinite(value) or not 1.0 <= value <= MAX_WINDOW_SECONDS:
                raise ValueError()
        except (ValueError, TypeError):
            self.error = "统计区间请输入 1～3600 秒"
            self.serial += 1
            return False
        self.pie_seconds = value
        self.next_sample = float(now)
        self.editing = False
        self.error = ""
        self.serial += 1
        return True

    def _apply_edit(self, now):
        return (
            self.set_pie_seconds(self.edit_text, now)
            if self.edit_target == "window"
            else self.set_interval(self.edit_text, now)
        )

    def action(self, key, now):
        if key == "chart:mode":
            self.mode = "pie" if self.mode == "line" else "line"
            self.editing = False
            self.error = ""
            self.serial += 1
            return
        if key.startswith("chart:field:"):
            field = key[len("chart:field:") :]
            if field not in {f["key"] for f in self.fields}:
                return
            if field in self.enabled:
                self.enabled.remove(field)
            else:
                self.enabled.add(field)
        elif key in ("chart:interval", "chart:window"):
            self.edit_target = "window" if key.endswith("window") else "interval"
            self.editing = True
            self.edit_text = ""
            self.error = ""
        elif key in ("chart:minus", "chart:plus"):
            self.set_interval(
                max(
                    0.5,
                    min(60.0, self.interval + (-0.5 if key.endswith("minus") else 0.5)),
                ),
                now,
            )
        elif key in ("chart:window_minus", "chart:window_plus"):
            self.set_pie_seconds(
                max(
                    1.0,
                    min(
                        MAX_WINDOW_SECONDS,
                        self.pie_seconds + (-1 if key.endswith("minus") else 1),
                    ),
                ),
                now,
            )
        elif key == "chart:previous":
            self.page = max(0, self.page - 1)
        elif key == "chart:next":
            self.page = min((len(self.fields) - 1) // PAGE_SIZE, self.page + 1)
        elif key == "chart:apply" and self.editing:
            self._apply_edit(now)
        self.serial += 1

    def keyboard(self, key, now):
        if not self.editing:
            return False
        if key in (0xFF0D, 0xFF8D):
            self._apply_edit(now)
        elif key == 0xFF1B:
            self.editing = False
            self.error = ""
        elif key in (0xFF08, 0xFFFF):
            self.edit_text = self.edit_text[:-1]
        elif (48 <= key <= 57 or key == 46) and len(self.edit_text) < 8:
            self.edit_text += chr(key)
        elif 0xFFB0 <= key <= 0xFFB9 and len(self.edit_text) < 8:
            self.edit_text += str(key - 0xFFB0)
        elif key == 0xFFAE and len(self.edit_text) < 8:
            self.edit_text += "."
        self.serial += 1
        return True

    def snapshot(self):
        end = self.sampled_at if self.sampled_at is not None else 0.0
        return dict(
            interval=self.interval,
            fields=self.fields,
            enabled=sorted(self.enabled),
            history=list(self.history),
            percentage_history=list(self.history),
            latest=dict(self.latest),
            page=self.page,
            page_size=PAGE_SIZE,
            editing=self.editing,
            edit_text=self.edit_text,
            error=self.error,
            serial=self.serial,
            window_seconds=WINDOW_SECONDS,
            window_start=end - WINDOW_SECONDS,
            window_end=end,
            percentage_denominator=self.denominator,
            denominator_mode=self.denominator_mode,
            data_note=self.data_note,
            denominator_label=(
                "累计已观测总数"
                if self.denominator_mode == "observed_total"
                else "当前在场总数"
            ),
            percentage_unit="%",
            unrecognized_scope="final observed departure without confirmation plus explicit legacy unresolved; pending/identity loss excluded",
            mode=self.mode,
            pie_seconds=self.pie_seconds,
            pie=dict(self.pie),
            rate=self.rate,
            rate_history=list(self.rate_history),
            rate_summary=dict(self.rate_summary),
            rate_unit="件/秒",
            edit_target=self.edit_target,
            awaiting_count_reset=self.awaiting_reset,
            reset_requests=self.reset_requests,
        )


def short_number(value):
    if value is None:
        return "—"
    if abs(value) >= 1000000:
        return f"{value/1000000:.1f}M"
    if abs(value) >= 10000:
        return f"{value/1000:.1f}k"
    return str(int(value)) if value == int(value) else f"{value:.1f}"


def draw_chart(view, regions, chart, px, py, pw, bottom):
    from PIL import ImageDraw

    muted = (145, 168, 188, 255)
    view.fill(None, (px, py, pw, max(200, bottom - py)), (22, 34, 49))
    pie = chart["mode"] == "pie"
    pie_palette = (
        pie_colors(tuple(s["key"] for s in chart["pie"].get("sectors", [])))
        if pie
        else {}
    )
    view.text(
        None,
        "姿态饼图" if pie else "姿态折线 · 近5秒",
        px + 16,
        py + 12,
        pw - 156,
        size=22,
    )
    view.button(
        None,
        regions,
        "chart:mode",
        "切换折线" if pie else "切换饼图",
        px + pw - 132,
        py + 8,
        116,
        pie,
    )
    rate = short_number(chart["rate"])
    view.text(
        None,
        f"近5秒平均通过：{rate} 件/秒",
        px + 16,
        py + 51,
        pw - 32,
        size=18,
        color=(255, 172, 72, 255),
    )
    cy = py + 82
    view.button(None, regions, "chart:minus", "-", px + 16, cy, 42)
    editing_interval = chart["editing"] and chart["edit_target"] == "interval"
    value = (
        (chart["edit_text"] or "输入秒数")
        if editing_interval
        else f"更新 {chart['interval']:g} 秒/次"
    )
    view.button(
        None, regions, "chart:interval", value, px + 66, cy, pw - 196, editing_interval
    )
    view.button(None, regions, "chart:plus", "+", px + pw - 122, cy, 42)
    view.button(
        None, regions, "chart:apply", "确定", px + pw - 74, cy, 64, chart["editing"]
    )
    if pie:
        cy += 48
        view.button(None, regions, "chart:window_minus", "-", px + 16, cy, 42)
        editing_window = chart["editing"] and chart["edit_target"] == "window"
        value = (
            (chart["edit_text"] or "输入区间秒数")
            if editing_window
            else f"区间 {chart['pie_seconds']:g} 秒"
        )
        view.button(
            None, regions, "chart:window", value, px + 66, cy, pw - 146, editing_window
        )
        view.button(None, regions, "chart:window_plus", "+", px + pw - 74, cy, 64)
    hint = chart["error"] or (
        "计数清零中…"
        if chart["awaiting_count_reset"]
        else (
            "输入后回车确认，Esc 取消"
            if chart["editing"]
            else (
                "分母：区间内通过总数"
                if pie
                else "占比左轴 · 件/秒右轴 · 累计观测为分母"
            )
        )
    )
    view.text(
        None,
        hint,
        px + 16,
        cy + 43,
        pw - 32,
        size=18,
        color=(248, 162, 104, 255) if chart["error"] else muted,
    )
    top = py + (210 if pie else 164)
    pages = max(1, (len(chart["fields"]) + PAGE_SIZE - 1) // PAGE_SIZE)
    rows = min(
        5,
        (
            len(
                chart["fields"][
                    chart["page"] * PAGE_SIZE : (chart["page"] + 1) * PAGE_SIZE
                ]
            )
            + 1
        )
        // 2,
    )
    reserve = 86 + rows * 32 + (42 if pages > 1 else 0)
    height = max(90, min(250, bottom - top - reserve))
    left, right, low = px + 56, px + pw - 58, top + height
    draw = ImageDraw.Draw(view.canvas)
    enabled = set(chart["enabled"])
    if pie:
        summary = chart["pie"]
        diameter = min(pw - 48, height)
        cx = px + pw / 2
        cy = top + height / 2
        box = (
            cx - diameter / 2,
            cy - diameter / 2,
            cx + diameter / 2,
            cy + diameter / 2,
        )
        if summary.get("total") and summary.get("complete"):
            angle = -90.0
            for sector in summary["sectors"]:
                if sector["count"] <= 0:
                    continue
                end = angle + sector["percentage"] * 3.6
                shade = pie_palette[sector["key"]]
                draw.pieslice(
                    box,
                    angle,
                    end,
                    fill=(*shade, 255),
                    outline=(12, 20, 31, 255),
                    width=3,
                )
                draw.pieslice(box, angle, end, outline=(235, 242, 248, 255), width=1)
                angle = end
            hole = diameter * 0.24
            draw.ellipse(
                (cx - hole, cy - hole, cx + hole, cy + hole), fill=(22, 34, 49)
            )
            if hole >= 34:
                draw.text(
                    (cx, cy - 15),
                    "区间总数",
                    font=view.fonts[18],
                    fill=muted,
                    anchor="mm",
                )
                draw.text(
                    (cx, cy + 13),
                    short_number(summary["total"]) + "件",
                    font=view.fonts[22],
                    fill=(235, 243, 250, 255),
                    anchor="mm",
                )
            else:
                draw.text(
                    (cx, cy),
                    short_number(summary["total"]) + "件",
                    font=view.fonts[18],
                    fill=(235, 243, 250, 255),
                    anchor="mm",
                )
        else:
            draw.ellipse(box, outline=(48, 65, 83), width=3)
            note = (
                "计数清零中…"
                if chart["awaiting_count_reset"]
                else (
                    "等待工件数据"
                    if not summary.get("available")
                    else (
                        "区间内暂无工件"
                        if summary.get("complete")
                        else "等待完整统计区间"
                    )
                )
            )
            view.text(None, note, px + 34, cy - 12, pw - 68, size=18, color=muted)
    else:
        for value in (0, 25, 50, 75, 100):
            y = low - height * value / 100
            draw.line((left, y, right, y), fill=(48, 65, 83), width=1)
            view.text(None, f"{value}%", px + 3, y - 10, 51, size=18, color=muted)
        rates = [v for _, v in chart["rate_history"] if v is not None]
        rate_max = max(1.0, math.ceil(max(rates, default=0) * 1.2))
        for value in (0.0, rate_max / 2, rate_max):
            view.text(
                None,
                short_number(value),
                right + 7,
                low - height * value / rate_max - 10,
                48,
                size=18,
                color=(255, 172, 72, 255),
            )
        start, end = chart["window_start"], chart["window_end"]
        enabled = set(chart["enabled"])
        plotted = False
        for field in chart["fields"]:
            if field["key"] not in enabled:
                continue
            scale = rate_max if field["key"] == "rate:throughput" else 100.0
            segment = []
            for when, row in chart["history"]:
                val = row.get(field["key"])
                if val is None or not start <= when <= end:
                    if len(segment) > 1:
                        draw.line(segment, fill=field["color"], width=2)
                    segment = []
                    continue
                segment.append(
                    (
                        left + (when - start) / WINDOW_SECONDS * (right - left),
                        low - val / scale * height,
                    )
                )
            if len(segment) > 1:
                draw.line(segment, fill=field["color"], width=2)
                plotted = True
            if segment:
                x, y = segment[-1]
                draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=field["color"])
                plotted = True
        if not plotted:
            view.text(
                None,
                chart["data_note"] or "正在积累区间数据…",
                left + 8,
                top + height // 2 - 12,
                right - left - 16,
                size=18,
                color=muted,
            )
        view.text(None, "-5 s", left, low + 7, 70, size=18, color=muted)
        view.text(
            None, "-2.5 s", (left + right) // 2 - 28, low + 7, 80, size=18, color=muted
        )
        view.text(None, "现在", right - 42, low + 7, 48, size=18, color=muted)
    cy = low + 42
    view.text(
        None,
        "累计计数 / 区间占比" if pie else "累计计数（勾选显示折线）",
        px + 16,
        cy,
        pw - 32,
        size=18,
    )
    pie_percent = {s["key"]: s["percentage"] for s in chart["pie"].get("sectors", [])}
    for index, field in enumerate(
        chart["fields"][chart["page"] * PAGE_SIZE : (chart["page"] + 1) * PAGE_SIZE]
    ):
        x = px + 16 + (index % 2) * (pw - 32) // 2
        y = cy + 30 + (index // 2) * 32
        cell = (pw - 32) // 2
        on = field["key"] in enabled
        if pie:
            on = field["key"] in pie_percent
            shade = pie_palette.get(field["key"], (128, 144, 164))
            draw.ellipse(
                (x + 3, y + 7, x + 15, y + 19),
                fill=shade if on else None,
                outline=shade,
            )
        else:
            draw.rounded_rectangle(
                (x, y + 4, x + 18, y + 22), radius=5, outline=field["color"], width=2
            )
            if on:
                draw.line(
                    (x + 3, y + 12, x + 8, y + 18, x + 15, y + 8),
                    fill=field["color"],
                    width=2,
                )
        label = field["title"] + " " + short_number(chart["latest"].get(field["key"]))
        if field["key"] == "rate:throughput":
            label += "件/秒"
        if pie and field["key"] in pie_percent:

            percent = f"{pie_percent[field['key']]:.1f}%"
            font = view.fonts[18]
            width = cell - 98
            if font.getlength(label) > width:
                title = field["title"]
                suffix = " " + short_number(chart["latest"].get(field["key"]))
                while title and font.getlength(title + "…" + suffix) > width:
                    title = title[:-1]
                label = title + "…" + suffix
            view.text(
                None, label, x + 25, y, width, size=18, color=(225, 235, 244, 255)
            )
            draw.text(
                (x + cell - 5, y + 16),
                percent,
                font=font,
                fill=(245, 249, 253, 255),
                anchor="rm",
            )
        else:
            view.text(
                None,
                label,
                x + 25,
                y,
                cell - 27,
                size=18,
                color=(*field["color"], 255) if on else muted,
            )
        if not pie:
            regions.append(
                dict(key="chart:field:" + field["key"], box=(x, y, cell, 30))
            )
    if pages > 1:
        y = cy + 30 + rows * 32
        view.button(None, regions, "chart:previous", "上一页", px + 16, y, 96)
        view.text(None, f"{chart['page']+1} / {pages}", px + 128, y + 5, 100, size=18)
        view.button(None, regions, "chart:next", "下一页", px + pw - 112, y, 96)
