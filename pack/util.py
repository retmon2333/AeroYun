import os
import sys
from fake_useragent import UserAgent

UA = UserAgent()
import ctypes
from ctypes import wintypes
from PyQt5.QtWidgets import QGraphicsOpacityEffect
from PyQt5.QtCore import QPropertyAnimation


def get_headers() -> dict:
    return {
        "User-Agent": UA.random
    }


class PathManager:
    """路径管理类：确保物理读写路径完全精准"""

    @staticmethod
    def get_exe_dir():
        if "__compiled__" in globals() or getattr(sys, 'frozen', False):
            return os.path.dirname(os.path.abspath(sys.executable))
        import __main__
        if hasattr(__main__, "__file__"):
            return os.path.dirname(os.path.abspath(__main__.__file__))
        return os.path.dirname(os.path.abspath(__file__))


# ================= 👑 Windows 底层全局空闲检测引擎 =================
class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("dwTime", wintypes.DWORD)
    ]


# 强制规定返回值为无符号 32 位整数，防止运行 49 天后溢出为负数报错
ctypes.windll.kernel32.GetTickCount.restype = wintypes.DWORD


def get_windows_idle_time():
    """获取 Windows 系统全局键鼠空闲时间（返回毫秒）"""
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
        millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
        return millis
    return 0
# ================================================================
