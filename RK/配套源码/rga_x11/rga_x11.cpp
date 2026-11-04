#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <X11/keysym.h>
#include <X11/extensions/XShm.h>
#include <X11/extensions/shape.h>
#include <X11/cursorfont.h>
#include <sys/ipc.h>
#include <sys/shm.h>
#include <rga/im2d.hpp>
#include <rga/rga.h>
#include <chrono>
#include <cstring>
#include <cstdio>
#include <deque>
#include <array>
#include <unordered_map>
#include <algorithm>
#include <cstdint>

struct Slot {
    XImage *image = nullptr;
    XShmSegmentInfo shm{};
    bool attached = false, busy = false;
    unsigned long long ui = 0;
    unsigned source = 0;
    rga_buffer_handle_t rga = 0;
};
struct Viewer {
    Cursor arrow = 0, hand = 0;
    int cursor = -1;
    Display *d = nullptr;
    Window window = 0;
    GC gc = 0;
    int x = 0, y = 0, w = 0, h = 0, aw = 0, ah = 0, event = 0;
    Slot slots[8];
    std::deque<std::array<int, 4>> input;
    std::unordered_map<const void *, rga_buffer_handle_t> sources;
    unsigned submitted = 0, completed = 0, last_completed = 0;
    unsigned long long visible_ui = 0;
    double rga_ms = 0;
    char error[256]{};
};

static void rounded_window(Viewer *v) {
    int shape_event, shape_error;
    if (!XShapeQueryExtension(v->d, &shape_event, &shape_error))
        return;
    int r = std::min(24, std::min(v->w, v->h) / 2);
    Pixmap mask = XCreatePixmap(v->d, v->window, v->w, v->h, 1);
    GC gc = XCreateGC(v->d, mask, 0, nullptr);
    XSetForeground(v->d, gc, 0);
    XFillRectangle(v->d, mask, gc, 0, 0, v->w, v->h);
    XSetForeground(v->d, gc, 1);
    XFillRectangle(v->d, mask, gc, r, 0, v->w - 2 * r, v->h);
    XFillRectangle(v->d, mask, gc, 0, r, v->w, v->h - 2 * r);
    for (int xx : {0, v->w - 2 * r})
        for (int yy : {0, v->h - 2 * r})
            XFillArc(v->d, mask, gc, xx, yy, 2 * r, 2 * r, 0, 360 * 64);
    XShapeCombineMask(v->d, v->window, ShapeBounding, 0, 0, mask, ShapeSet);
    XShapeCombineMask(v->d, v->window, ShapeInput, 0, 0, mask, ShapeSet);
    XFreeGC(v->d, gc);
    XFreePixmap(v->d, mask);
}
extern "C" void rv_cursor(void *handle, int hand) {
    auto *v = (Viewer *)handle;
    if (v->cursor == hand)
        return;
    v->cursor = hand;
    XDefineCursor(v->d, v->window, hand ? v->hand : v->arrow);
    XFlush(v->d);
}

static void pump(Viewer *v) {
    while (XPending(v->d)) {
        XEvent e;
        XNextEvent(v->d, &e);
        if (e.type == VisibilityNotify && e.xvisibility.state == VisibilityFullyObscured &&
            v->w == v->aw && v->h == v->ah) {
            XRaiseWindow(v->d, v->window);
            XSetInputFocus(v->d, v->window, RevertToParent, CurrentTime);
            XFlush(v->d);
        }
        if (e.type == v->event + ShmCompletion) {
            auto *c = (XShmCompletionEvent *)&e;
            for (auto &s : v->slots)
                if (s.shm.shmseg == c->shmseg) {
                    s.busy = false;
                    v->completed++;
                    v->last_completed = s.source;
                }
        }
        if (e.type == ButtonPress || e.type == ButtonRelease || e.type == MotionNotify) {
            int x = e.type == MotionNotify ? e.xmotion.x : e.xbutton.x,
                y = e.type == MotionNotify ? e.xmotion.y : e.xbutton.y;
            std::array<int, 4> item = {e.type == ButtonPress     ? 1
                                       : e.type == ButtonRelease ? 3
                                                                 : 2,
                                       x, y, 0};
            if (e.type == MotionNotify && !v->input.empty() && v->input.back()[0] == 2)
                v->input.back() = item;
            else
                v->input.push_back(item);
        }
        if (e.type == LeaveNotify)
            v->input.push_back({5, -1, -1, 0});
        if (e.type == KeyPress)
            v->input.push_back({4, 0, 0, (int)XLookupKeysym(&e.xkey, 0)});
    }
}
static void free_slots(Viewer *v) {
    XSync(v->d, False);
    pump(v);
    for (auto &s : v->slots) {
        if (s.rga)
            releasebuffer_handle(s.rga);
        if (s.attached)
            XShmDetach(v->d, &s.shm);
    }
    XSync(v->d, False);
    for (auto &s : v->slots) {
        if (s.image) {
            s.image->data = nullptr;
            XDestroyImage(s.image);
        }
        if (s.shm.shmaddr && s.shm.shmaddr != (char *)-1)
            shmdt(s.shm.shmaddr);
        s = Slot{};
    }
}
static bool make_slots(Viewer *v) {
    int screen = DefaultScreen(v->d);
    auto visual = DefaultVisual(v->d, screen);
    int depth = DefaultDepth(v->d, screen);
    if (depth != 24 || visual->red_mask != 0xff0000 || visual->green_mask != 0xff00 ||
        visual->blue_mask != 0xff) {
        snprintf(v->error, 256, "Unsupported X11 visual");
        return false;
    }
    for (auto &s : v->slots) {
        s.image = XShmCreateImage(v->d, visual, depth, ZPixmap, nullptr, &s.shm, v->w, v->h);
        if (!s.image || s.image->bits_per_pixel != 32 || s.image->bytes_per_line != v->w * 4)
            return false;
        s.shm.shmid = shmget(IPC_PRIVATE, v->w * v->h * 4, IPC_CREAT | 0600);
        if (s.shm.shmid < 0)
            return false;
        s.shm.shmaddr = (char *)shmat(s.shm.shmid, nullptr, 0);
        s.shm.readOnly = False;
        if (s.shm.shmaddr == (char *)-1)
            return false;
        s.image->data = s.shm.shmaddr;
        if (!XShmAttach(v->d, &s.shm))
            return false;
        s.attached = true;
        XSync(v->d, False);
        shmctl(s.shm.shmid, IPC_RMID, nullptr);
        s.rga = importbuffer_virtualaddr(s.image->data, v->w * v->h * 4);
        if (!s.rga) {
            snprintf(v->error, 256, "RGA destination import failed");
            return false;
        }
    }
    return true;
}
extern "C" void rv_close(void *handle) {
    auto *v = (Viewer *)handle;
    if (!v)
        return;
    if (v->d) {
        free_slots(v);
        for (auto &p : v->sources)
            releasebuffer_handle(p.second);
        if (v->arrow)
            XFreeCursor(v->d, v->arrow);
        if (v->hand)
            XFreeCursor(v->d, v->hand);
        if (v->gc)
            XFreeGC(v->d, v->gc);
        if (v->window)
            XDestroyWindow(v->d, v->window);
        XCloseDisplay(v->d);
    }
    delete v;
}
extern "C" void *rv_open(int x, int y, int w, int h) {
    auto *v = new Viewer();
    v->x = x;
    v->y = y;
    v->w = w;
    v->h = h;
    v->aw = w;
    v->ah = h;
    v->d = XOpenDisplay(nullptr);
    if (!v->d || !XShmQueryExtension(v->d)) {
        rv_close(v);
        return nullptr;
    }
    v->event = XShmGetEventBase(v->d);
    int screen = DefaultScreen(v->d);
    v->window = XCreateSimpleWindow(v->d, RootWindow(v->d, screen), x, y, w, h, 0, 0, 0);
    v->gc = XCreateGC(v->d, v->window, 0, nullptr);
    XSetWindowAttributes a{};
    a.override_redirect = True;
    XChangeWindowAttributes(v->d, v->window, CWOverrideRedirect, &a);
    XStoreName(v->d, v->window, "FPGA Vision 60 FPS");
    XSelectInput(v->d, v->window,
                 KeyPressMask | ButtonPressMask | ButtonReleaseMask | PointerMotionMask |
                     LeaveWindowMask | StructureNotifyMask | VisibilityChangeMask);
    if (!make_slots(v)) {
        fprintf(stderr, "rv_open: %s\n", v->error);
        rv_close(v);
        return nullptr;
    }
    v->arrow = XCreateFontCursor(v->d, XC_left_ptr);
    v->hand = XCreateFontCursor(v->d, XC_hand2);
    rounded_window(v);
    XMapRaised(v->d, v->window);
    XSync(v->d, False);
    return v;
}
extern "C" int rv_resize(void *handle, int x, int y, int w, int h) {
    auto *v = (Viewer *)handle;
    if (!v)
        return -1;
    if (w > v->aw || h > v->ah)
        return -1;
    v->visible_ui = 0;
    v->x = x;
    v->y = y;
    v->w = w;
    v->h = h;
    XMoveResizeWindow(v->d, v->window, x, y, w, h);
    rounded_window(v);
    XFlush(v->d);
    return 0;
}
extern "C" int rv_event(void *handle, int *out) {
    auto *v = (Viewer *)handle;
    pump(v);
    if (v->input.empty())
        return 0;
    auto a = v->input.front();
    v->input.pop_front();
    for (int i = 0; i < 4; i++)
        out[i] = a[i];
    return 1;
}
extern "C" const char *rv_error(void *handle) {
    return ((Viewer *)handle)->error;
}
extern "C" void rv_stats(void *handle, double *out) {
    auto *v = (Viewer *)handle;
    pump(v);
    out[0] = v->submitted;
    out[1] = v->completed;
    out[2] = v->last_completed;
    out[3] = v->rga_ms / std::max(1u, v->submitted);
}
static void line(Viewer *v, Slot *s, int x0, int y0, int x1, int y1, unsigned color) {
    x0 = std::max(0, x0);
    y0 = std::max(0, y0);
    x1 = std::min(v->w - 1, x1);
    y1 = std::min(v->h - 1, y1);
    if (x1 < x0 || y1 < y0)
        return;
    auto *dst = (unsigned *)s->image->data;
    for (int x = x0; x <= x1; x++) {
        dst[y0 * v->aw + x] = color;
        dst[y1 * v->aw + x] = color;
    }
    for (int y = y0; y <= y1; y++) {
        dst[y * v->aw + x0] = color;
        dst[y * v->aw + x1] = color;
    }
}
static int show_frame(void *handle, const unsigned char *rgb, const unsigned char *ui,
                      unsigned long long version, unsigned source, int source_w, int source_h,
                      int x, int y, int w, int h, int count, const float *boxes,
                      const int *confirmed, const int *roi, const uint64_t *) {
    auto *v = (Viewer *)handle;
    pump(v);
    Slot *s = nullptr;
    for (auto &a : v->slots)
        if (!a.busy) {
            s = &a;
            break;
        }
    if (!s)
        return 0;
    bool update_ui = v->visible_ui != version;
    if (update_ui) {
        for (int row = 0; row < v->h; row++)
            memcpy(s->image->data + row * v->aw * 4, ui + row * v->w * 4, v->w * 4);
        s->ui = version;
    }
    auto found = v->sources.find(rgb);
    rga_buffer_handle_t sh;
    if (found == v->sources.end()) {
        sh = importbuffer_virtualaddr((void *)rgb, source_w * source_h * 3);
        if (!sh) {
            snprintf(v->error, 256, "RGA RGB source import failed");
            return -1;
        }
        v->sources[rgb] = sh;
    } else
        sh = found->second;
    auto src = wrapbuffer_handle(sh, source_w, source_h, RK_FORMAT_RGB_888);
    auto dst = wrapbuffer_handle(s->rga, v->aw, v->ah, RK_FORMAT_BGRA_8888);
    im_rect sr{0, 0, source_w, source_h}, dr{x, y, w, h};
    rga_buffer_t pat{};
    im_rect pr{};
    auto before = std::chrono::steady_clock::now();
    IM_STATUS status = improcess(src, dst, pat, sr, dr, pr, IM_SYNC);
    if (status != IM_STATUS_SUCCESS) {
        snprintf(v->error, 256, "RGA: %s", imStrError(status));
        return -2;
    }
    v->rga_ms +=
        std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - before)
            .count();
    for (int dy = 0; dy < h; dy += 28)
        for (int z = -1; z <= 1; z++)
            line(v, s, x + w / 2 + z, y + dy, x + w / 2 + z, y + std::min(h - 1, dy + 17),
                 0xff00ddc2);
    if (roi && roi[2] > 0 && roi[3] > 0) {
        int l = x + roi[0] * w / source_w, t = y + roi[1] * h / source_h,
            r = x + (roi[0] + roi[2]) * w / source_w - 1,
            b = y + (roi[1] + roi[3]) * h / source_h - 1;
        for (int z = 0; z < 2; z++)
            line(v, s, l + z, t + z, r - z, b - z, 0xff00ddc2);
    }
    for (int i = 0; i < count; i++) {
        const float *b = boxes + i * 4;
        int l = x + (int)(b[0] * w / 1920), t = y + (int)(b[1] * h / 1080),
            r = x + (int)(b[2] * w / 1920), bottom = y + (int)(b[3] * h / 1080);

        for (int z = 0; z < 2; z++)
            line(v, s, l + z, t + z, r - z, bottom - z,
                 (confirmed[i] & 2) ? 0xffff55ee : ((confirmed[i] & 1) ? 0xff41f399 : 0xffffd13e));
    }

    auto *pixels = (unsigned *)s->image->data;
    auto *background = (const unsigned *)ui;
    int radius = std::min(22, std::min(w, h) / 2);
    for (int iy = 0; iy < radius; iy++)
        for (int ix = 0; ix < radius; ix++) {
            int dx = radius - 1 - ix, dy = radius - 1 - iy;
            if (dx * dx + dy * dy < radius * radius)
                continue;
            for (int xx : {x + ix, x + w - 1 - ix})
                for (int yy : {y + iy, y + h - 1 - iy})
                    if (xx >= 0 && yy >= 0 && xx < v->w && yy < v->h)
                        pixels[yy * v->aw + xx] = background[yy * v->w + xx];
        }
    s->busy = true;
    s->source = source;
    if (!XShmPutImage(v->d, v->window, v->gc, s->image, update_ui ? 0 : x, update_ui ? 0 : y,
                      update_ui ? 0 : x, update_ui ? 0 : y, update_ui ? v->w : w,
                      update_ui ? v->h : h, True)) {
        s->busy = false;
        snprintf(v->error, 256, "XShmPutImage submission failed");
        return -3;
    }
    v->visible_ui = version;
    XFlush(v->d);
    v->submitted++;
    return 1;
}

extern "C" int rv_show(void *handle, const unsigned char *rgb, const unsigned char *ui,
                       unsigned long long version, unsigned source, int source_w, int source_h,
                       int x, int y, int w, int h, int count, const float *boxes,
                       const int *confirmed) {
    return show_frame(handle, rgb, ui, version, source, source_w, source_h, x, y, w, h, count,
                      boxes, confirmed, nullptr, nullptr);
}
extern "C" unsigned rv_overlay_api_version() {
    return 1;
}
extern "C" int rv_show_overlay_v1(void *handle, const unsigned char *rgb, const unsigned char *ui,
                                  unsigned long long version, unsigned source, int source_w,
                                  int source_h, int x, int y, int w, int h, int count,
                                  const float *boxes, const int *confirmed, const int *roi,
                                  const uint64_t *ids) {
    return show_frame(handle, rgb, ui, version, source, source_w, source_h, x, y, w, h, count,
                      boxes, confirmed, roi, ids);
}
