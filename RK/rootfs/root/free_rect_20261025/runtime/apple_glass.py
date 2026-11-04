from functools import lru_cache
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageFont


@lru_cache(maxsize=1)
def fonts():
    paths = (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    )
    path = next(p for p in paths if Path(p).is_file())
    return {size: ImageFont.truetype(path, size) for size in (18, 20, 22, 28)}


@lru_cache(maxsize=3)
def backdrop(raw, size, w, h):
    if raw:
        small = Image.frombytes("RGB", tuple(size), raw).filter(
            ImageFilter.GaussianBlur(7)
        )
        image = small.resize((w, h), Image.Resampling.BILINEAR).convert("RGBA")
    else:
        image = Image.new("RGBA", (w, h), (31, 43, 64, 255))
    image = Image.alpha_composite(image, Image.new("RGBA", (w, h), (8, 15, 29, 208)))
    return image


@lru_cache(maxsize=64)
def masks(w, h, radius):
    mask = Image.new("L", (w, h))
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius, fill=255)
    shadow = Image.new("L", (w + 24, h + 24))
    shadow.paste(mask, (12, 14))
    shadow = shadow.filter(ImageFilter.GaussianBlur(7)).point(
        lambda value: round(value * 0.30)
    )
    return mask, shadow


def begin(view, w, h, state):
    state = state or {}
    view.hover_key = state.get("hover")
    view.glass_base = backdrop(
        state.get("backdrop"), state.get("backdrop_size", (128, 72)), w, h
    )
    view.canvas = view.glass_base.copy()
    draw = ImageDraw.Draw(view.canvas)
    draw.rounded_rectangle(
        (1, 1, w - 2, h - 2), 24, outline=(169, 188, 216, 55), width=1
    )


def surface(view, box, color=(22, 34, 49), radius=22, hover=False):
    x, y, w, h = map(int, box)
    if w <= 0 or h <= 0:
        return
    radius = min(radius, w // 2, h // 2)
    mask, shadow = masks(w, h, radius)
    tile = Image.new("RGBA", shadow.size, (0, 0, 0, 0))
    tile.putalpha(shadow)
    view.canvas.alpha_composite(tile, (x - 12, y - 10))
    base = getattr(view, "glass_base", view.canvas).crop((x, y, x + w, y + h))
    tint = (64, 87, 120, 172) if hover else (*tuple(color[:3]), 190)
    panel = Image.alpha_composite(base, Image.new("RGBA", (w, h), tint))
    edge = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(edge)
    draw.rounded_rectangle(
        (0, 0, w - 1, h - 1),
        radius,
        outline=(199, 219, 247, 140 if hover else 66),
        width=1,
    )
    draw.line(
        (radius, 1, w - radius, 1), fill=(235, 243, 255, 115 if hover else 55), width=1
    )
    panel = Image.alpha_composite(panel, edge)
    view.canvas.paste(panel, (x, y), mask)


def text(
    view,
    value,
    x,
    y,
    width,
    color=(229, 236, 245, 255),
    background=(0, 0, 0, 0),
    size=20,
):
    width = max(0, min(int(width), view.canvas.width - int(x)))
    if width <= 0:
        return
    font = view.fonts[size]
    value = str(value).replace("\n", " ")
    if font.getlength(value) > width:
        lo, hi = 0, len(value)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if font.getlength(value[:mid] + "…") <= width:
                lo = mid
            else:
                hi = mid - 1
        value = value[:lo] + "…" if font.getlength("…") <= width else ""
    tile = Image.new("RGBA", (width, size + 12), background)
    ImageDraw.Draw(tile).text((0, 0), value, font=font, fill=color)
    view.canvas.alpha_composite(tile, (int(x), int(y)))
    view.rasterizations = getattr(view, "rasterizations", 0) + 1


def button(view, regions, key, label, x, y, width, active=False):
    hover = getattr(view, "hover_key", None) == key
    surface(
        view, (x, y, width, 40), (29, 103, 156) if active else (43, 56, 77), 12, hover
    )
    font = view.fonts[18]
    label = str(label)
    usable = max(0, width - 20)
    if font.getlength(label) > usable:
        while label and font.getlength(label + "…") > usable:
            label = label[:-1]
        label += "…"

    tx = x + (width - min(usable, font.getlength(label))) / 2
    text(view, label, tx, y + 5, width - (tx - x) - 8, size=18)
    regions.append(dict(key=key, box=(x, y, width, 40)))


def frame_state(view, control, now):
    if now >= getattr(view, "glass_backdrop_due", 0):
        rgb = control.last_overview.rgb
        image = rgb[
            :: max(1, (rgb.shape[0] + 71) // 72), :: max(1, (rgb.shape[1] + 127) // 128)
        ]
        view.glass_backdrop = image.tobytes()
        view.glass_backdrop_size = (image.shape[1], image.shape[0])
        view.glass_backdrop_due = now + 0.5
    return dict(
        hover=getattr(view, "hover_key", None),
        backdrop=view.glass_backdrop,
        backdrop_size=view.glass_backdrop_size,
    )
