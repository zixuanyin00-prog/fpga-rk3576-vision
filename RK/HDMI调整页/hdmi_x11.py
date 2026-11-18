                                                                   

import ctypes as C
from contextlib import contextmanager


Window = C.c_ulong
Display = C.c_void_p
Int = C.c_int
UInt = C.c_uint
Pointer = C.POINTER


class WindowAttributes(C.Structure):
    _fields_ = [
        ('x', Int), ('y', Int), ('width', Int), ('height', Int),
        ('border_width', Int), ('depth', Int), ('visual', C.c_void_p),
        ('root', Window), ('window_class', Int), ('bit_gravity', Int),
        ('win_gravity', Int), ('backing_store', Int),
        ('backing_planes', C.c_ulong), ('backing_pixel', C.c_ulong),
        ('save_under', Int), ('colormap', C.c_ulong), ('map_installed', Int),
        ('map_state', Int), ('all_event_masks', C.c_long),
        ('your_event_mask', C.c_long), ('do_not_propagate_mask', C.c_long),
        ('override_redirect', Int), ('screen', C.c_void_p),
    ]


class XErrorEvent(C.Structure):
    _fields_ = [
        ('type', Int), ('display', Display), ('resourceid', Window),
        ('serial', C.c_ulong), ('error_code', C.c_ubyte),
        ('request_code', C.c_ubyte), ('minor_code', C.c_ubyte),
    ]


ErrorHandler = C.CFUNCTYPE(Int, Display, Pointer(XErrorEvent))


class PreviewWindow:
    def __init__(self):
        self.x11 = C.CDLL('libX11.so.6')
        signatures = {
            'XOpenDisplay': ([C.c_char_p], Display),
            'XCloseDisplay': ([Display], Int),
            'XDefaultRootWindow': ([Display], Window),
            'XQueryTree': ([Display, Window, Pointer(Window), Pointer(Window), Pointer(Pointer(Window)), Pointer(UInt)], Int),
            'XFetchName': ([Display, Window, Pointer(C.c_char_p)], Int),
            'XGetWindowAttributes': ([Display, Window, Pointer(WindowAttributes)], Int),
            'XFree': ([C.c_void_p], Int),
            'XCreateSimpleWindow': ([Display, Window, Int, Int, UInt, UInt, UInt, C.c_ulong, C.c_ulong], Window),
            'XReparentWindow': ([Display, Window, Window, Int, Int], Int),
            'XMoveResizeWindow': ([Display, Window, Int, Int, UInt, UInt], Int),
            'XMapRaised': ([Display, Window], Int),
            'XUnmapWindow': ([Display, Window], Int),
            'XSetInputFocus': ([Display, Window, Int, C.c_ulong], Int),
            'XSetErrorHandler': ([C.c_void_p], C.c_void_p),
            'XSync': ([Display, Int], Int),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.x11, name)
            function.argtypes = arguments
            function.restype = result
        self.display = self.x11.XOpenDisplay(None)
        if not self.display:
            raise RuntimeError('无法连接 HDMI 的 X11 显示服务')
        self.root = self.x11.XDefaultRootWindow(self.display)
        self.preview = None
        self.containers = {}

    @contextmanager
    def _check(self):
                                                                         
        errors = []

        @ErrorHandler
        def collect(display, event):
            if display == self.display:
                errors.append(event.contents.error_code)
                return 0
            return ErrorHandler(previous)(display, event) if previous else 0

        previous = self.x11.XSetErrorHandler(C.cast(collect, C.c_void_p))
        try:
            yield errors
        finally:
            self.x11.XSync(self.display, 0)
            self.x11.XSetErrorHandler(previous)

    def _visible_size(self, xid):
        attributes = WindowAttributes()
        if self.x11.XGetWindowAttributes(self.display, xid, C.byref(attributes)):
            if attributes.map_state == 2 and attributes.override_redirect:
                return attributes.width, attributes.height
        return None

    def find(self):
                                                                         
        with self._check():
            if self.preview:
                size = self._visible_size(self.preview)
                if size:
                    return self.preview, *size
                self.preview = None
            rooted, parent = Window(), Window()
            children = Pointer(Window)()
            count = UInt()
            if not self.x11.XQueryTree(self.display, self.root, C.byref(rooted), C.byref(parent), C.byref(children), C.byref(count)):
                return None
            try:
                for index in range(count.value - 1, -1, -1):
                    xid = children[index]
                    name = C.c_char_p()
                    if not self.x11.XFetchName(self.display, xid, C.byref(name)):
                        continue
                    preview_name = name.value == b'FPGA Vision 60 FPS'
                    self.x11.XFree(name)
                    if preview_name:
                        size = self._visible_size(xid)
                        if size:
                            self.preview = xid
                            return xid, *size
            finally:
                if children:
                    self.x11.XFree(children)
        return None

    def attach(self, xid, x, y, width, height, *, visible=True):
                                                                           
        with self._check() as errors:
            container = self.x11.XCreateSimpleWindow(self.display, xid, x, y, width, height, 0, 0, 0x101B29)
            if visible:
                self.x11.XMapRaised(self.display, container)
        if errors:
            raise RuntimeError('HDMI 预览窗口已关闭')
        key = hex(container)
        self.containers[key] = (x, y, width, height, visible)
        return key

    def reparent(self, window, container):
                                                                  
        with self._check() as errors:
            self.x11.XReparentWindow(self.display, window, int(container, 16), 0, 0)
        return not errors

    def place(self, container, x, y, width, height, *, visible=True):
        previous = self.containers[container]
        updated = x, y, width, height, visible
        if updated == previous:
            return True
        xid = int(container, 16)
        with self._check() as errors:
            if previous[:4] != updated[:4]:
                self.x11.XMoveResizeWindow(self.display, xid, x, y, width, height)
            if previous[4] != visible:
                operation = self.x11.XMapRaised if visible else self.x11.XUnmapWindow
                operation(self.display, xid)
        if errors:
            return False
        self.containers[container] = updated
        return True

    def hide(self, container):
        return self.place(container, *self.containers[container][:4], visible=False)

    def focus(self, window):
                                                                           
        with self._check() as errors:
            self.x11.XSetInputFocus(self.display, window, 2, 0)
        return not errors

    def close(self):
        if self.display:
            self.x11.XCloseDisplay(self.display)
            self.display = None
