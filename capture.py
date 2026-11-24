import ctypes
from ctypes import wintypes
import sys

from ingest import ReadResult


def capture_game():
    from PIL import ImageGrab

    if sys.platform != "win32":
        raise ValueError("游戏窗口截图适用于 Windows")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    hwnd = user32.GetForegroundWindow()
    title = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(wintypes.HWND(hwnd), title, len(title))
    if "鸣潮" not in title.value and "wuthering waves" not in title.value.lower():
        raise ValueError("请切到鸣潮窗口并打开一件声骸的详情")
    image = ImageGrab.grab(window=hwnd).convert("RGB")
    if image.getbbox() is None:
        raise ValueError("游戏窗口截图为空，请尝试窗口模式或导入截图")
    return image


def collect_with_hotkey(stop, progress, record, take_photo=capture_game):
    if sys.platform != "win32":
        raise ValueError("快捷键采集适用于 Windows")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    hotkey_id, ctrl_shift_no_repeat, f8 = 1, 0x4006, 0x77
    if not user32.RegisterHotKey(None, hotkey_id, ctrl_shift_no_repeat, f8):
        raise ValueError("无法注册 Ctrl+Shift+F8，请关闭占用此快捷键的工具")
    try:
        results = []
        progress("在游戏中选中 +25 声骸并按 Ctrl+Shift+F8")
        msg = wintypes.MSG()
        while not stop.wait(.04):
            if not user32.PeekMessageW(ctypes.byref(msg), None, 0x0312, 0x0312, 1):
                continue
            try:
                image = take_photo()
                results.append(record(image))
            except (OSError, ValueError) as error:
                progress(str(error))
                continue
            progress(f"已入库 {results.count(ReadResult.SAVED)} 件 · Ctrl+Shift+F8 继续")
        return results
    finally:
        user32.UnregisterHotKey(None, hotkey_id)
