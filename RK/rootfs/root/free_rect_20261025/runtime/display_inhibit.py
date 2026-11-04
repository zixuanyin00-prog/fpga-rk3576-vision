import ctypes as C


class DisplayInhibit:
    def __init__(self):
        self.x = C.CDLL("libX11.so.6")
        self.ss = C.CDLL("libXss.so.1")
        self.ext = C.CDLL("libXext.so.6")
        self.x.XOpenDisplay.argtypes = [C.c_char_p]
        self.x.XOpenDisplay.restype = C.c_void_p
        self.x.XCloseDisplay.argtypes = [C.c_void_p]
        self.x.XCloseDisplay.restype = C.c_int
        self.x.XSync.argtypes = [C.c_void_p, C.c_int]
        self.x.XSync.restype = C.c_int
        self.x.XResetScreenSaver.argtypes = [C.c_void_p]
        self.x.XResetScreenSaver.restype = C.c_int
        self.ss.XScreenSaverQueryVersion.argtypes = [
            C.c_void_p,
            C.POINTER(C.c_int),
            C.POINTER(C.c_int),
        ]
        self.ss.XScreenSaverQueryVersion.restype = C.c_int
        self.ss.XScreenSaverSuspend.argtypes = [C.c_void_p, C.c_int]
        self.ss.XScreenSaverSuspend.restype = None
        self.ext.DPMSDisable.argtypes = [C.c_void_p]
        self.ext.DPMSDisable.restype = C.c_int
        self.display = self.x.XOpenDisplay(None)
        if not self.display:
            raise RuntimeError("Cannot open the display for screen-saver inhibition")
        major = C.c_int()
        minor = C.c_int()
        if not self.ss.XScreenSaverQueryVersion(
            self.display, C.byref(major), C.byref(minor)
        ) or (major.value, minor.value) < (1, 1):
            self.x.XCloseDisplay(self.display)
            self.display = None
            raise RuntimeError("X11 Screen Saver extension 1.1 is required")
        self.ss.XScreenSaverSuspend(self.display, 1)
        self.x.XResetScreenSaver(self.display)
        self.ext.DPMSDisable(self.display)
        self.x.XSync(self.display, 0)
        self.state = dict(
            active=True,
            screen_saver_timer_suspended=True,
            dpms_timer_suspended=True,
            extension_version=[major.value, minor.value],
            scope="display_worker_connection",
        )

    def close(self):
        self.ss.XScreenSaverSuspend(self.display, 0)
        self.x.XSync(self.display, 0)
        self.x.XCloseDisplay(self.display)
        self.display = None
