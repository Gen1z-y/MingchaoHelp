"""Windows-only primitives for the opt-in inventory scanner."""

import ctypes
from ctypes import wintypes as W
import os
from pathlib import Path
from subprocess import list2cmdline
import sys
from time import sleep


class MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", W.LONG), ("dy", W.LONG), ("data", W.DWORD),
        ("flags", W.DWORD), ("time", W.DWORD), ("extra", ctypes.c_size_t),
    ]


class Input(ctypes.Structure):
    _fields_ = [("type", W.DWORD), ("mouse", MouseInput)]


class ShellExecuteInfo(ctypes.Structure):
    _fields_ = [
        ("size", W.DWORD), ("mask", W.ULONG), ("window", W.HWND),
        ("verb", W.LPCWSTR), ("file", W.LPCWSTR), ("arguments", W.LPCWSTR),
        ("directory", W.LPCWSTR), ("show", ctypes.c_int), ("instance", W.HINSTANCE),
        ("id_list", ctypes.c_void_p), ("file_class", W.LPCWSTR), ("key", W.HKEY),
        ("hotkey", W.DWORD), ("icon", W.HANDLE), ("process", W.HANDLE),
    ]


class Process:
    def __init__(self, handle):
        self.handle = handle
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.WaitForSingleObject.argtypes = (W.HANDLE, W.DWORD)
        self.api.WaitForSingleObject.restype = W.DWORD
        self.api.CloseHandle.argtypes = (W.HANDLE,)

    @classmethod
    def open(cls, pid):
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = (W.DWORD, W.BOOL, W.DWORD)
        api.OpenProcess.restype = W.HANDLE
        handle = api.OpenProcess(0x100000, False, pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        return cls(handle)

    def running(self):
        return self.api.WaitForSingleObject(self.handle, 0) == 0x102

    def close(self):
        self.api.CloseHandle(self.handle)


def launch_elevated(script, arguments):
    if sys.platform != "win32":
        raise ValueError("自动点选适用于 Windows")
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.ShellExecuteExW.argtypes = (ctypes.POINTER(ShellExecuteInfo),)
    shell.ShellExecuteExW.restype = W.BOOL
    python = Path(sys.executable).resolve()
    frozen = getattr(sys, "frozen", False)
    if not frozen:
        windowless = python.with_name("pythonw.exe")
        if windowless.is_file():
            python = windowless
    info = ShellExecuteInfo()
    info.size = ctypes.sizeof(info)
    info.mask = 0x40 | 0x100  # Return a process handle and complete launch synchronously.
    info.verb = "runas"
    info.file = str(python)
    info.arguments = list2cmdline(arguments if frozen else ["-I", str(script), *arguments])
    info.directory = str(python.parent if frozen else Path(script).parent)
    ole = ctypes.WinDLL("ole32")
    initialized = ole.CoInitializeEx(None, 2) >= 0
    reset_environment = "PYINSTALLER_RESET_ENVIRONMENT"
    previous_reset = os.environ.get(reset_environment)
    if frozen:
        # The elevated worker extracts its own copy instead of borrowing the UI process's files.
        os.environ[reset_environment] = "1"
    try:
        if not shell.ShellExecuteExW(ctypes.byref(info)):
            code = ctypes.get_last_error()
            if code == 1223:
                raise ValueError("已取消 Windows 管理员授权") from None
            raise ctypes.WinError(code)
    finally:
        if frozen:
            if previous_reset is None:
                os.environ.pop(reset_environment, None)
            else:
                os.environ[reset_environment] = previous_reset
        if initialized:
            ole.CoUninitialize()
    if not info.process:
        raise ValueError("Windows 未返回自动扫描进程")
    return Process(info.process)


class NativeGame:
    SIZE = (1280, 720)

    def __init__(self):
        self.api = ctypes.WinDLL("user32", use_last_error=True)
        self.api.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        self.api.GetForegroundWindow.restype = W.HWND
        self.api.GetWindowTextW.argtypes = (W.HWND, W.LPWSTR, ctypes.c_int)
        self.api.GetWindowThreadProcessId.argtypes = (W.HWND, ctypes.POINTER(W.DWORD))
        self.api.GetClientRect.argtypes = (W.HWND, ctypes.POINTER(W.RECT))
        self.api.ClientToScreen.argtypes = (W.HWND, ctypes.POINTER(W.POINT))
        self.api.GetAsyncKeyState.restype = ctypes.c_short
        self.api.SendInput.argtypes = (W.UINT, ctypes.POINTER(Input), ctypes.c_int)
        self.api.SendInput.restype = W.UINT
        self.api.WindowFromPoint.argtypes = (W.POINT,)
        self.api.WindowFromPoint.restype = W.HWND
        self.api.GetAncestor.argtypes = (W.HWND, W.UINT)
        self.api.GetAncestor.restype = W.HWND
        self.window = None
        self.rect = None

    def foreground_matches(self):
        hwnd = self.api.GetForegroundWindow()
        if not hwnd:
            return False
        if self.window:
            return hwnd == self.window
        title = ctypes.create_unicode_buffer(256)
        self.api.GetWindowTextW(hwnd, title, len(title))
        if title.value.strip().lower() not in {"鸣潮", "wuthering waves"}:
            return False
        pid = W.DWORD()
        self.api.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (W.DWORD, W.BOOL, W.DWORD)
        kernel.OpenProcess.restype = W.HANDLE
        kernel.QueryFullProcessImageNameW.argtypes = (
            W.HANDLE, W.DWORD, W.LPWSTR, ctypes.POINTER(W.DWORD),
        )
        kernel.CloseHandle.argtypes = (W.HANDLE,)
        proc = kernel.OpenProcess(0x1000, False, pid.value)
        if not proc:
            return False
        try:
            path = ctypes.create_unicode_buffer(32768)
            capacity = W.DWORD(len(path))
            if not kernel.QueryFullProcessImageNameW(proc, 0, path, ctypes.byref(capacity)):
                return False
            if Path(path.value).name.lower() != "client-win64-shipping.exe":
                return False
        finally:
            kernel.CloseHandle(proc)
        self.window = hwnd
        return True

    def active_rect(self):
        if not self.foreground_matches():
            raise ValueError("鸣潮窗口已离开前台，自动扫描已停止")
        bounds = W.RECT()
        origin = W.POINT()
        if not (self.api.GetClientRect(self.window, ctypes.byref(bounds)) and
                self.api.ClientToScreen(self.window, ctypes.byref(origin))):
            raise ValueError("无法读取鸣潮窗口位置")
        width, height = bounds.right, bounds.bottom
        if not height or width < 960 or abs(width / height - 16 / 9) > .06:
            raise ValueError("自动扫描需要 16:9 窗口，客户区宽度至少 960 像素")
        return origin.x, origin.y, origin.x + width, origin.y + height

    def stopped(self):
        return self.api.GetAsyncKeyState(0x1B) < 0

    def frame(self):
        from PIL import Image, ImageGrab

        rect = self.active_rect()
        if self.rect and rect != self.rect:
            raise ValueError("窗口位置或尺寸已变动，自动扫描已停止")
        self.rect = rect
        image = ImageGrab.grab(bbox=rect, all_screens=True)
        return image.convert("RGB").resize(self.SIZE, Image.Resampling.BILINEAR)

    def point(self, x, y):
        rect = self.active_rect()
        if rect != self.rect:
            raise ValueError("窗口位置或尺寸已变动，自动扫描已停止")
        left, top, right, bottom = rect
        point = W.POINT(left + round(x * (right - left) / self.SIZE[0]),
                        top + round(y * (bottom - top) / self.SIZE[1]))
        target = self.api.WindowFromPoint(point)
        if not target or self.api.GetAncestor(target, 2) != self.window:
            raise ValueError("鸣潮窗口被覆盖，自动扫描已停止")
        if self.stopped() or not self.api.SetCursorPos(point.x, point.y):
            raise ValueError("定位鼠标时扫描已停止")

    def send(self, events):
        if self.stopped() or not self.foreground_matches():
            raise ValueError("发送输入前扫描已停止")
        inputs = (Input * len(events))(*[
            Input(0, MouseInput(0, 0, data & 0xFFFFFFFF, flags, 0, 0))
            for flags, data in events
        ])
        amount = self.api.SendInput(len(events), inputs, ctypes.sizeof(Input))
        if amount != len(events):
            if amount and events[0][0] == 2:
                self.release_mouse()
            raise ValueError("Windows 未接受鼠标输入，自动扫描已停止")

    def release_mouse(self):
        # Release even if focus changed or Esc was pressed during a drag.
        release = Input(0, MouseInput(0, 0, 0, 4, 0, 0))
        if self.api.SendInput(1, ctypes.byref(release), ctypes.sizeof(Input)) != 1:
            raise ValueError("Windows 未接受鼠标松开事件，请手动松开鼠标左键")

    def click(self, x, y):
        self.point(x, y)
        self.send(((2, 0), (4, 0)))

    def drag_scrollbar(self, x, start, target):
        self.point(x, start)
        self.send(((2, 0),))
        try:
            sleep(.07)
            steps = min(10, max(1, (abs(target - start) + 5) // 6))
            for step in range(1, steps + 1):
                self.point(x, start + round((target - start) * step / steps))
                sleep(.025)
            sleep(.05)
        finally:
            self.release_mouse()
