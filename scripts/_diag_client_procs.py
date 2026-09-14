# -*- coding: utf-8 -*-
"""枚举百度客户端各进程的模块，找出真正承载下载引擎（YunDls/kernel/netbase）的那个。"""
import ctypes
import subprocess
import sys

k32 = ctypes.windll.kernel32
k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p


class ME(ctypes.Structure):
    _fields_ = [("dwSize", ctypes.c_ulong), ("th32ModuleID", ctypes.c_ulong),
                ("th32ProcessID", ctypes.c_ulong), ("GlblcntUsage", ctypes.c_ulong),
                ("ProccntUsage", ctypes.c_ulong), ("modBaseAddr", ctypes.c_void_p),
                ("modBaseSize", ctypes.c_ulong), ("hModule", ctypes.c_void_p),
                ("szModule", ctypes.c_wchar * 256),
                ("szExePath", ctypes.c_wchar * 260)]

TH = 0x8 | 0x10


def mods(pid):
    s = k32.CreateToolhelp32Snapshot(TH, pid)
    if not s or s == ctypes.c_void_p(-1).value:
        return []
    out = []
    me = ME()
    me.dwSize = ctypes.sizeof(me)
    ok = k32.Module32FirstW(ctypes.c_void_p(s), ctypes.byref(me))
    while ok:
        out.append((me.szModule, me.modBaseSize))
        ok = k32.Module32NextW(ctypes.c_void_p(s), ctypes.byref(me))
    k32.CloseHandle(ctypes.c_void_p(s))
    return out


KEY = ("YunDls", "kernel", "netbase", "YunLogic", "hook32", "hook64",
       "speedpatch", "winmm", "YunUtility", "cloudpic")


def main():
    r = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True,
                       encoding="mbcs", errors="replace")
    for line in (r.stdout or "").splitlines():
        parts = [p.strip('"') for p in line.split(",")]
        if len(parts) < 2:
            continue
        # 大小写必须不敏感：baidunetdiskhost.exe 是小写开头，写死 "Baidu"
        # 会把它整个漏掉——而它正是唯一承载下载引擎的进程。
        if not any(x in parts[0].lower() for x in ("baidu", "yundetect")):
            continue
        try:
            pid = int(parts[1])
        except ValueError:
            continue
        mm = mods(pid)
        hit = [f"{m}" for m, _ in mm
               if any(k.lower() in m.lower() for k in KEY)]
        print(f"pid={pid:<8} {parts[0]:<26} mods={len(mm):<4} -> "
              f"{', '.join(hit) if hit else '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
