# -*- coding: utf-8 -*-
"""macOS 输入后端 —— Quartz (CoreGraphics) 事件注入 + 事件监听。

依赖：pip install pyobjc-framework-Quartz
首次运行需要在 系统设置 → 隐私与安全性 → 辅助功能 里勾选本程序，
否则既发不出按键、也收不到中键事件。

平台差异说明：
  · Mac 的 F9 默认是媒体键，所以主快捷键用 ⌘⇧V，F9 作为备选。
  · Mac 浏览器中键不会触发"自动滚动"，所以单击无需拦截补发，
    只在识别到双击时吞掉第二次点击。
"""

import ctypes
import threading
import time

try:
    import Quartz
except ImportError:  # 打包后不会走到这里；源码运行时给个明确提示
    Quartz = None

# ---- 虚拟键码 (HIToolbox/Events.h) ----
KEY = {
    "a": 0x00, "v": 0x09, "enter": 0x24, "tab": 0x30, "space": 0x31,
    "back": 0x33, "esc": 0x35, "cmd": 0x37, "shift": 0x38, "ctrl": 0x3B,
    "alt": 0x3A, "f9": 0x65, "home": 0x73, "del": 0x75, "end": 0x77,
}

# ---- 修饰键 flag (CGEventFlags) ----
FLAG = {
    "shift": 0x00020000,
    "ctrl": 0x00040000,
    "alt": 0x00080000,
    "cmd": 0x00100000,
}

BUTTON_MIDDLE = 2
KEYCODE_F9 = 0x65
KEYCODE_ESC = 0x35
KEYCODE_V = 0x09


def accessibility_trusted():
    """是否已获得"辅助功能"授权。"""
    try:
        path = ("/System/Library/Frameworks/ApplicationServices.framework"
                "/ApplicationServices")
        lib = ctypes.CDLL(path)
        lib.AXIsProcessTrusted.restype = ctypes.c_bool
        return bool(lib.AXIsProcessTrusted())
    except Exception:
        return True


class _MacHook(threading.Thread):
    """CGEventTap：中键双击 / ⌘⇧V / F9 / ESC。"""

    DOUBLE_GAP = 0.30

    def __init__(self, on_double, on_hotkey, on_abort):
        super().__init__(daemon=True, name="MacHook")
        self.on_double = on_double
        self.on_hotkey = on_hotkey
        self.on_abort = on_abort
        self.armed = True
        self.ok = None
        self._tap = None
        self._last_middle = None
        self._swallow_up = False

    def run(self):
        if Quartz is None:
            self.ok = False
            return
        try:
            mask = ((1 << Quartz.kCGEventOtherMouseDown)
                    | (1 << Quartz.kCGEventOtherMouseUp)
                    | (1 << Quartz.kCGEventKeyDown))
            tap = Quartz.CGEventTapCreate(
                Quartz.kCGSessionEventTap,
                Quartz.kCGHeadInsertEventTap,
                Quartz.kCGEventTapOptionDefault,
                mask,
                self._callback,
                None,
            )
            if not tap:
                self.ok = False
                return
            self._tap = tap
            self.ok = True
            src = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
            Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetCurrent(), src,
                                      Quartz.kCFRunLoopCommonModes)
            Quartz.CGEventTapEnable(tap, True)
            Quartz.CFRunLoopRun()
        except Exception:
            self.ok = False

    def stop(self):
        try:
            if self._tap:
                Quartz.CGEventTapEnable(self._tap, False)
            Quartz.CFRunLoopStop(Quartz.CFRunLoopGetMain())
        except Exception:
            pass

    def _callback(self, proxy, type_, event, refcon):
        try:
            if type_ == Quartz.kCGEventOtherMouseDown:
                btn = Quartz.CGEventGetIntegerValueField(
                    event, Quartz.kCGMouseEventButtonNumber)
                if btn == BUTTON_MIDDLE and self.armed:
                    now = time.monotonic()
                    if (self._last_middle is not None
                            and now - self._last_middle <= self.DOUBLE_GAP):
                        self._last_middle = None
                        self._swallow_up = True
                        threading.Thread(target=self.on_double, daemon=True).start()
                        return None          # 吞掉第二次按下
                    self._last_middle = now
                return event

            if type_ == Quartz.kCGEventOtherMouseUp:
                if self._swallow_up:
                    self._swallow_up = False
                    return None
                return event

            if type_ == Quartz.kCGEventKeyDown:
                code = Quartz.CGEventGetIntegerValueField(
                    event, Quartz.kCGKeyboardEventKeycode)
                flags = Quartz.CGEventGetFlags(event)
                cmd = bool(flags & FLAG["cmd"])
                shift = bool(flags & FLAG["shift"])
                if code == KEYCODE_ESC:
                    threading.Thread(target=self.on_abort, daemon=True).start()
                elif code == KEYCODE_F9:
                    threading.Thread(target=self.on_hotkey, daemon=True).start()
                elif code == KEYCODE_V and cmd and shift:
                    threading.Thread(target=self.on_hotkey, daemon=True).start()
                    return None          # 别让 ⌘⇧V 继续冒泡
                return event
        except Exception:
            pass
        return event


class MacBackend:
    name = "mac"
    platform_label = "macOS"
    hotkey_label = "⌘⇧V"
    trigger_label = "双击鼠标中键"

    def __init__(self):
        self.hook = None
        self._flags = 0

    # ---------- 底层 ----------
    def _post(self, code, down=True):
        if Quartz is None:
            return
        ev = Quartz.CGEventCreateKeyboardEvent(None, code, down)
        if ev is None:
            return
        if self._flags:
            Quartz.CGEventSetFlags(ev, self._flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)

    # ---------- 按键 ----------
    def down(self, name):
        if name in FLAG:
            self._flags |= FLAG[name]
        self._post(KEY[name], True)

    def up(self, name):
        if name in FLAG:
            self._flags &= ~FLAG[name]
        self._post(KEY[name], False)

    def tap(self, name, pause=0.004):
        self.down(name)
        self.up(name)
        if pause:
            time.sleep(pause)

    def type_char(self, ch, compat=False):
        if Quartz is None:
            return
        units = len(ch.encode("utf-16-le")) // 2
        for is_down in (True, False):
            ev = Quartz.CGEventCreateKeyboardEvent(None, 0, is_down)
            if ev is None:
                continue
            Quartz.CGEventKeyboardSetUnicodeString(ev, units, ch)
            if self._flags:
                Quartz.CGEventSetFlags(ev, self._flags)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)

    def select_all_delete(self):
        self.down("cmd")
        self.tap("a", 0)
        self.up("cmd")
        time.sleep(0.06)
        self.tap("back")

    # ---------- 环境 ----------
    def foreground(self):
        """返回 (pid, 应用名)。用于输入中途被切走时自动停手。"""
        if Quartz is None:
            return 0, ""
        try:
            wins = Quartz.CGWindowListCopyWindowInfo(
                Quartz.kCGWindowListOptionOnScreenOnly
                | Quartz.kCGWindowListExcludeDesktopElements,
                Quartz.kCGNullWindowID)
            for w in (wins or []):
                if w.get("kCGWindowLayer") == 0:
                    pid = w.get("kCGWindowOwnerPID", 0)
                    owner = w.get("kCGWindowOwnerName", "") or ""
                    return int(pid), str(owner)
        except Exception:
            pass
        return 0, ""

    def esc_pressed(self):
        if Quartz is None:
            return False
        try:
            return bool(Quartz.CGEventSourceKeyState(
                Quartz.kCGEventSourceStateCombinedSessionState, KEYCODE_ESC))
        except Exception:
            return False

    # ---------- 钩子 ----------
    def start_hooks(self, on_double, on_hotkey, on_abort):
        self.hook = _MacHook(on_double, on_hotkey, on_abort)
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
        if not accessibility_trusted():
            return ("需要在「系统设置 → 隐私与安全性 → 辅助功能」中勾选本程序，"
                    "否则无法自动输入")
        return None
