# -*- coding: utf-8 -*-
"""Windows 输入后端 —— SendInput 注入 + WH_*_LL 全局钩子。

只依赖标准库（ctypes），不需要额外安装任何东西。
"""

import ctypes
import ctypes.wintypes as wt
import threading
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14

WM_QUIT = 0x0012
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MBUTTONDBLCLK = 0x0209

MAGIC = 0x43545950  # 'CTYP' 标记本程序注入的事件

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040

VK = {
    "back": 0x08, "tab": 0x09, "enter": 0x0D, "shift": 0x10, "ctrl": 0x11,
    "esc": 0x1B, "end": 0x23, "home": 0x24, "space": 0x20, "del": 0x2E,
    "a": 0x41, "f9": 0x78,
}
EXTENDED_VKS = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2C, 0x2D, 0x2E}


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wt.POINT), ("mouseData", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM, wt.LPARAM)

user32.SetWindowsHookExW.restype = wt.HHOOK
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wt.UINT
user32.VkKeyScanW.restype = ctypes.c_short
user32.VkKeyScanW.argtypes = [wt.WCHAR]
user32.MapVirtualKeyW.restype = wt.UINT
user32.MapVirtualKeyW.argtypes = [wt.UINT, wt.UINT]
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short


def _send(*items):
    arr = (INPUT * len(items))(*items)
    user32.SendInput(len(items), arr, ctypes.sizeof(INPUT))


def _kbd(vk=0, scan=0, flags=0):
    inp = INPUT()
    inp.type = 1
    inp.ki = KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=MAGIC)
    return inp


def _mouse(flags):
    inp = INPUT()
    inp.type = 0
    inp.mi = MOUSEINPUT(dx=0, dy=0, mouseData=0, dwFlags=flags, time=0, dwExtraInfo=MAGIC)
    return inp


class _Hook(threading.Thread):
    """全局低级钩子：中键双击 / F9 / ESC。"""

    DOUBLE_GAP = 0.28
    REPLAY_DELAY = 0.32

    def __init__(self, on_double, on_hotkey, on_abort):
        super().__init__(daemon=True, name="WinHook")
        self.on_double = on_double
        self.on_hotkey = on_hotkey
        self.on_abort = on_abort
        self.armed = True
        self.ok = None
        self._tid = None
        self._ready = threading.Event()
        self._h_mouse = None
        self._h_kbd = None
        self._first_down = 0.0
        self._pending = False
        self._timer = None
        self._lock = threading.Lock()

    def run(self):
        self._tid = kernel32.GetCurrentThreadId()
        self._mouse_proc = HOOKPROC(self._mouse_cb)
        self._kbd_proc = HOOKPROC(self._kbd_cb)
        self._h_mouse = user32.SetWindowsHookExW(WH_MOUSE_LL, self._mouse_proc, None, 0)
        self._h_kbd = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kbd_proc, None, 0)
        self.ok = bool(self._h_mouse and self._h_kbd)
        self._ready.set()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        if self._h_mouse:
            user32.UnhookWindowsHookEx(self._h_mouse)
        if self._h_kbd:
            user32.UnhookWindowsHookEx(self._h_kbd)

    def stop(self):
        self._ready.wait(1.0)
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)

    def _cancel_timer(self):
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _schedule_replay(self):
        self._cancel_timer()
        self._timer = threading.Timer(self.REPLAY_DELAY, self._replay_once)
        self._timer.daemon = True
        self._timer.start()

    def _replay_once(self):
        with self._lock:
            if not self._pending:
                return
            self._pending = False
        _send(_mouse(MOUSEEVENTF_MIDDLEDOWN), _mouse(MOUSEEVENTF_MIDDLEUP))

    def _mouse_cb(self, nCode, wParam, lParam):
        try:
            if nCode == 0 and self.armed:
                info = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                if info.dwExtraInfo != MAGIC:
                    if wParam == WM_MBUTTONDOWN:
                        now = time.monotonic()
                        with self._lock:
                            if self._pending and (now - self._first_down) <= self.DOUBLE_GAP:
                                self._pending = False
                                fire = True
                            else:
                                self._first_down = now
                                self._pending = True
                                fire = False
                        if fire:
                            self._cancel_timer()
                            threading.Thread(target=self.on_double, daemon=True).start()
                        else:
                            self._schedule_replay()
                        return 1
                    if wParam in (WM_MBUTTONUP, WM_MBUTTONDBLCLK):
                        return 1
        except Exception:
            pass
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    def _kbd_cb(self, nCode, wParam, lParam):
        try:
            if nCode == 0 and wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                info = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if info.dwExtraInfo != MAGIC:
                    if info.vkCode == VK["f9"]:
                        threading.Thread(target=self.on_hotkey, daemon=True).start()
                    elif info.vkCode == VK["esc"]:
                        threading.Thread(target=self.on_abort, daemon=True).start()
        except Exception:
            pass
        return user32.CallNextHookEx(None, nCode, wParam, lParam)


class WinBackend:
    name = "win"
    platform_label = "Windows"
    hotkey_label = "F9"
    trigger_label = "双击鼠标中键"

    def __init__(self):
        self.hook = None

    # ---------- 按键 ----------
    def down(self, name):
        vk = VK[name]
        flags = KEYEVENTF_EXTENDEDKEY if vk in EXTENDED_VKS else 0
        _send(_kbd(vk=vk, scan=user32.MapVirtualKeyW(vk, 0), flags=flags))

    def up(self, name):
        vk = VK[name]
        flags = KEYEVENTF_KEYUP | (KEYEVENTF_EXTENDEDKEY if vk in EXTENDED_VKS else 0)
        _send(_kbd(vk=vk, scan=user32.MapVirtualKeyW(vk, 0), flags=flags))

    def tap(self, name, pause=0.004):
        self.down(name)
        self.up(name)
        if pause:
            time.sleep(pause)

    def type_char(self, ch, compat=False):
        if compat:
            res = user32.VkKeyScanW(ch)
            if res != -1:
                vk = res & 0xFF
                mods = (res >> 8) & 0xFF
                hold = []
                if mods & 1:
                    hold.append(VK["shift"])
                if mods & 2:
                    hold.append(VK["ctrl"])
                for h in hold:
                    self.down_by_vk(h)
                self.down_by_vk(vk)
                self.up_by_vk(vk)
                for h in reversed(hold):
                    self.up_by_vk(h)
                return
        raw = ch.encode("utf-16-le")
        items = []
        for i in range(0, len(raw), 2):
            unit = int.from_bytes(raw[i:i + 2], "little")
            items.append(_kbd(scan=unit, flags=KEYEVENTF_UNICODE))
            items.append(_kbd(scan=unit, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
        _send(*items)

    def down_by_vk(self, vk):
        _send(_kbd(vk=vk, scan=user32.MapVirtualKeyW(vk, 0)))

    def up_by_vk(self, vk):
        _send(_kbd(vk=vk, scan=user32.MapVirtualKeyW(vk, 0), flags=KEYEVENTF_KEYUP))

    def select_all_delete(self):
        self.down("ctrl")
        self.tap("a", 0)
        self.up("ctrl")
        time.sleep(0.06)
        self.tap("del")

    # ---------- 环境 ----------
    def foreground(self):
        hwnd = user32.GetForegroundWindow()
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 2)
        user32.GetWindowTextW(hwnd, buf, n + 2)
        return hwnd, buf.value

    def esc_pressed(self):
        return bool(user32.GetAsyncKeyState(VK["esc"]) & 0x8000)

    # ---------- 钩子 ----------
    def start_hooks(self, on_double, on_hotkey, on_abort):
        self.hook = _Hook(on_double, on_hotkey, on_abort)
        self.hook.start()

    def set_armed(self, on):
        if self.hook:
            self.hook.armed = bool(on)

    @property
    def hook_ok(self):
        return self.hook.ok if self.hook else None

    def stop_hooks(self):
        if self.hook:
            self.hook.stop()

    def permission_hint(self):
        return None
