# -*- coding: utf-8 -*-
"""M1 实测第一步：客户端状态检查与 OpenSpeedy 清理决策。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "backend"))
from accel import openspeedy as osd  # noqa: E402


def exe_path_of(pid):
    """QueryFullProcessImageNameW → 进程可执行文件完整路径。"""
    k32 = ctypes.windll.kernel32
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    buf = ctypes.create_unicode_buffer(520)
    size = ctypes.c_ulong(520)
    ok = k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
    ctypes.windll.kernel32.CloseHandle(h)
    return buf.value if ok else ""


import ctypes  # noqa: E402

allp = osd.list_all_processes()
openspeedy = [p for p in allp if "openspeedy" in p["name"].lower()]
baidu = [p for p in allp if p["name"] in osd.BAIDU_PROCESS_NAMES]
main = [p for p in baidu if p["name"] == "BaiduNetdisk.exe"]

print("OpenSpeedy 进程:", [p["name"] for p in openspeedy] or "无")
print("百度客户端进程:", len(baidu), "个")
main_path = exe_path_of(main[0]["pid"]) if main else ""
print("主进程路径:", main_path)

# 检查 speedpatch 是否还在客户端里（用我们已构建的 booster.exe 的模块枚举逻辑
# —— 这里用 tasklist /M 的简单方式）
import subprocess  # noqa: E402
r = subprocess.run(["tasklist", "/M", "speedpatch32.dll", "/FO", "CSV", "/NH"],
                   capture_output=True, timeout=15,
                   encoding="mbcs", errors="replace")
infected = [line.split(",")[1].strip('"') for line in (r.stdout or "").splitlines()
            if line.strip()]
print("speedpatch32.dll 仍加载于:", infected or "无（干净）")

# 是否有活动下载（IO 速率）
s = osd.SpeedSampler()
pids = [p["pid"] for p in baidu]
s.sample(pids)
time.sleep(2)
speed = s.sample(pids)
print(f"当前客户端网络读速率: {speed/1e6:.2f} MB/s"
      f"（>0.05 说明有下载进行中）")
