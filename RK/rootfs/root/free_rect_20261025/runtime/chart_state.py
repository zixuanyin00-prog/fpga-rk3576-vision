from collections import deque
import colorsys
import hashlib
import math

GENERAL = [
    ("total", "总数"),
    ("current_present", "在场"),
    ("pending", "待识别"),
    ("unconfirmed", "无法确认"),
]


def color(key):
    hue = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return tuple(round(x * 255) for x in colorsys.hsv_to_rgb(hue, 0.55, 0.98))


def fields_for(counting):
    fields = [
        dict(key="stat:" + k, title=t, color=color("stat:" + k)) for k, t in GENERAL
    ]
    classes = counting.get("classes", [])
    return (
        fields[:1]
        + [
            dict(
                key="class:" + c["key"],
                title=c["title"],
                color=color("class:" + c["key"]),
            )
            for c in classes
        ]
        + fields[1:]
    )


def normalize_counting(counting):

    values = counting.get("class_counts", {})
    if isinstance(values, list):
        return dict(
            counting,
            classes=[dict(key=c["label"], title=c["title"]) for c in values],
            class_counts={c["label"]: c["count"] for c in values},
        )
    return counting


class ChartState:
    def __init__(self, interval=1.0):
        self.interval = interval
        self.history = deque(maxlen=120)
        self.fields = fields_for({})
        self.enabled = {"stat:total"}
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

    def update(self, counting, now):
        counting = normalize_counting(counting)
        schema = tuple((c["key"], c["title"]) for c in counting.get("classes", []))
        session = counting.get("session_id")
        if schema != self.schema or session != self.session:
            fields = fields_for(counting)
            self.schema, self.session = schema, session
            self.fields = fields
            keys = {f["key"] for f in fields}
            self.enabled.intersection_update(keys)
            self.enabled.update(
                f["key"]
                for f in fields
                if f["key"].startswith("class:") and f["key"] not in self.seen_fields
            )
            self.seen_fields.update(keys)
            self.history.clear()
            self.page = 0
            self.next_sample = now
            self.serial += 1
        if now < self.next_sample:
            return False
        values = {"stat:" + k: counting.get(k) for k, _ in GENERAL}
        values.update(
            {"class:" + k: v for k, v in counting.get("class_counts", {}).items()}
        )
        values = {
            k: (float(v) if isinstance(v, (int, float)) and math.isfinite(v) else None)
            for k, v in values.items()
        }
        self.latest = values
        self.history.append((now, values))
        self.next_sample = now + self.interval
        self.serial += 1
        return True

    def set_interval(self, value, now):
        try:
            number = float(value)
            if not math.isfinite(number) or not 0.5 <= number <= 3600:
                raise ValueError()
        except (TypeError, ValueError):
            self.error = "请输入 0.5～3600 秒"
            self.serial += 1
            return False
        self.interval = number
        self.next_sample = now + number
        self.editing = False
        self.error = ""
        self.serial += 1
        return True

    def action(self, key, now):
        if key.startswith("chart:field:"):
            name = key[len("chart:field:") :]
            if name in self.enabled:
                self.enabled.remove(name)
            else:
                self.enabled.add(name)
        elif key == "chart:interval":
            self.editing = True
            self.edit_text = ""
            self.error = ""
        elif key in ("chart:minus", "chart:plus"):
            self.set_interval(
                max(
                    0.5,
                    min(3600, self.interval + (-0.5 if key.endswith("minus") else 0.5)),
                ),
                now,
            )
        elif key == "chart:previous":
            self.page = max(0, self.page - 1)
        elif key == "chart:next":
            self.page = min((len(self.fields) - 1) // 8, self.page + 1)
        elif key == "chart:apply" and self.editing:
            self.set_interval(self.edit_text, now)
        self.serial += 1

    def keyboard(self, key, now):
        if not self.editing:
            return False
        if key in (0xFF0D, 0xFF8D):
            self.set_interval(self.edit_text, now)
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
        return dict(
            interval=self.interval,
            fields=self.fields,
            enabled=sorted(self.enabled),
            history=list(self.history),
            latest=self.latest,
            page=self.page,
            editing=self.editing,
            edit_text=self.edit_text,
            error=self.error,
            serial=self.serial,
        )


def short_number(value):
    if value is None:
        return "—"
    if abs(value) >= 1000000:
        return f"{value / 1000000:.1f}M"
    if abs(value) >= 10000:
        return f"{value / 1000:.1f}k"
    return str(int(value)) if value == int(value) else f"{value:.1f}"


def draw_chart(view, regions, chart, px, py, pw, bottom):
    from PIL import ImageDraw

    view.fill(None, (px, py, pw, max(200, bottom - py)), (22, 34, 49))
    view.text(None, "统计趋势", px + 16, py + 12, pw - 32, size=22)
    view.text(
        None,
        "累计计数 / 当前数量 · 最近 120 个采样点",
        px + 16,
        py + 44,
        pw - 32,
        size=18,
        color=(145, 168, 188, 255),
    )
    cy = py + 80
    view.button(None, regions, "chart:minus", "-", px + 16, cy, 42)
    value = (
        (chart["edit_text"] or "输入秒数")
        if chart["editing"]
        else f"{chart['interval']:g} 秒 / 次"
    )
    view.button(
        None, regions, "chart:interval", value, px + 66, cy, pw - 196, chart["editing"]
    )
    view.button(None, regions, "chart:plus", "+", px + pw - 122, cy, 42)
    view.button(
        None, regions, "chart:apply", "确定", px + pw - 74, cy, 64, chart["editing"]
    )
    hint = chart["error"] or (
        "输入后回车确认，Esc 取消"
        if chart["editing"]
        else "点击秒数输入，或用 - / + 调整"
    )
    view.text(
        None,
        hint,
        px + 16,
        cy + 44,
        pw - 32,
        size=18,
        color=(248, 162, 104, 255) if chart["error"] else (145, 168, 188, 255),
    )
    top = cy + 92
    height = max(100, min(330, bottom - top - 282))
    left, right, low = px + 58, px + pw - 20, top + height
    draw = ImageDraw.Draw(view.canvas)
    history = chart["history"]
    enabled = set(chart["enabled"])
    values = [
        v
        for _, row in history
        for k, v in row.items()
        if k in enabled and v is not None
    ]
    maximum = max(values, default=0)
    raw = max(1, maximum * 1.1) / 4
    scale = 10 ** math.floor(math.log10(raw))
    step = next((n * scale for n in (1, 2, 5, 10) if n * scale >= raw), 10 * scale)
    ceiling = step * 4
    for i in range(5):
        y = low - height * i / 4
        draw.line((left, y, right, y), fill=(48, 65, 83), width=1)
        view.text(
            None,
            short_number(ceiling * i / 4),
            px + 4,
            y - 10,
            52,
            size=18,
            color=(145, 168, 188, 255),
        )
    start = history[0][0] if history else 0
    end = history[-1][0] if history else 0
    span = max(chart["interval"], end - start)
    for field in chart["fields"]:
        if field["key"] not in enabled:
            continue
        segment = []
        for when, row in history:
            val = row.get(field["key"])
            if val is None:
                if len(segment) > 1:
                    draw.line(segment, fill=field["color"], width=2)
                segment = []
                continue
            point = (
                left + (when - start) / span * (right - left),
                low - val / ceiling * height,
            )
            segment.append(point)
        if len(segment) > 1:
            draw.line(segment, fill=field["color"], width=2)
        if segment:
            x, y = segment[-1]
            draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=field["color"])
    if not enabled:
        view.text(
            None,
            "在下方勾选要显示的字段",
            left + 8,
            top + height // 2 - 12,
            right - left - 16,
            size=18,
        )
    elif len(history) < 2:
        view.text(
            None,
            "正在积累数据…",
            left + 8,
            top + height // 2 - 12,
            right - left - 16,
            size=18,
        )
    view.text(
        None,
        f"{max(0,end-start):.1f} 秒前",
        left,
        low + 8,
        150,
        size=18,
        color=(145, 168, 188, 255),
    )
    view.text(
        None, "最新", right - 48, low + 8, 54, size=18, color=(145, 168, 188, 255)
    )
    cy = low + 48
    view.text(None, "显示字段", px + 16, cy, pw - 32, size=18)
    page = chart["page"]
    pages = max(1, (len(chart["fields"]) + 7) // 8)
    for i, field in enumerate(chart["fields"][page * 8 : page * 8 + 8]):
        x = px + 16 + (i % 2) * (pw - 32) // 2
        y = cy + 34 + (i // 2) * 40
        cell = (pw - 32) // 2
        on = field["key"] in enabled
        draw.rectangle((x, y + 5, x + 20, y + 25), outline=field["color"], width=2)
        if on:
            draw.line(
                (x + 4, y + 15, x + 9, y + 21, x + 17, y + 10),
                fill=field["color"],
                width=2,
            )
        label = field["title"] + " " + short_number(chart["latest"].get(field["key"]))
        view.text(
            None,
            label,
            x + 28,
            y,
            cell - 32,
            size=18,
            color=(*field["color"], 255) if on else (145, 168, 188, 255),
        )
        regions.append(dict(key="chart:field:" + field["key"], box=(x, y, cell, 36)))
    if pages > 1:
        y = cy + 198
        view.button(None, regions, "chart:previous", "上一页", px + 16, y, 96)
        view.text(None, f"{page+1} / {pages}", px + 128, y + 5, 100, size=18)
        view.button(None, regions, "chart:next", "下一页", px + pw - 112, y, 96)
