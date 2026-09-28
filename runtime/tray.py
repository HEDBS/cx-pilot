# -*- coding: utf-8 -*-
"""运行时外壳：单实例互斥锁 + 系统托盘。"""
import ctypes
import sys
import threading

_MUTEX_NAME = "Local\\cx-pilot_single_instance"
_mutex_handle = None


def acquire_single_instance():
    """已存在实例返回 False；成功持有返回 True（进程结束自动释放）。"""
    global _mutex_handle
    if sys.platform != "win32":
        return True
    kernel32 = ctypes.windll.kernel32
    _mutex_handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        return False
    return True


def build_tray(show_cb, quit_cb):
    """pystray 托盘；失败返回 None（不影响主程序）。"""
    try:
        import pystray
        from PIL import Image, ImageDraw

        def make_img(active=False):
            size = 64
            im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            d = ImageDraw.Draw(im)
            d.rounded_rectangle([2, 2, size - 2, size - 2], radius=14,
                                fill=(10, 132, 255, 255) if not active else (0, 122, 255, 255))
            d.text((14, 16), "C", fill=(255, 255, 255, 255))
            return im

        menu = pystray.Menu(
            pystray.MenuItem("显示主窗口", lambda i, it: show_cb(), default=True),
            pystray.MenuItem("退出", lambda i, it: quit_cb()),
        )
        icon = pystray.Icon("cx-pilot", make_img(), "cx-pilot 学习通作业助手", menu)
        threading.Thread(target=icon.run, daemon=True).start()
        return icon
    except Exception:
        return None
