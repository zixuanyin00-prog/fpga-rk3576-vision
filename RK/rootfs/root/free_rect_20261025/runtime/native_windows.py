TILE = (512, 288)
STRIDE = (256, 144)


def windows(width, height):
    if type(width) is not int or type(height) is not int or width < 512 or height < 288:
        raise ValueError("没有完整512×288原生窗口，禁止缩放或补像素")

    def starts(n, k):
        return sorted(set(range(0, n - k + 1, k // 2)) | {n - k})

    return [(x, y, 512, 288) for y in starts(height, 288) for x in starts(width, 512)]


def plan(rect):
    x, y, w, h = rect
    if any(type(v) is not int for v in rect) or not (
        0 <= x <= 1920 - w and 0 <= y <= 1080 - h
    ):
        raise ValueError("原生选区必须位于1920×1080内")
    return [(x + a, y + b, tw, th) for a, b, tw, th in windows(w, h)]


def accepts_tile_box(box, tile, rect):

    l, t, r, b = box
    x, y, w, h = tile
    rx, ry, rw, rh = rect
    if not (0 <= l < r <= w and 0 <= t < b <= h):
        return False
    return not (
        (x > rx and l < 2)
        or (y > ry and t < 2)
        or (x + w < rx + rw and r > w - 2)
        or (y + h < ry + rh and b > h - 2)
    )
