# -*- coding: utf-8 -*-
"""M1 冒烟测试：注入 → 改倍率 → revision 续接 → 自卸载 → 进程存活。"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "backend"))
from accel import openspeedy as osd  # noqa: E402

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "tools", "booster")
booster = os.path.join(TOOLS, "booster.exe")

# 启动一个 notepad 作为安全注入靶子
subprocess.Popen(["notepad.exe"], creationflags=subprocess.CREATE_NO_WINDOW)
time.sleep(2)

targets = [p for p in osd.list_all_processes()
           if p["name"].lower() == "notepad.exe"]
if not targets:
    print("FAIL: notepad 未启动")
    sys.exit(1)
pid = targets[0]["pid"]
print(f"[target] notepad pid={pid}")


def run(cmd):
    r = subprocess.run([booster] + cmd, capture_output=True, timeout=15,
                       encoding="mbcs", errors="replace")
    return (r.stdout or "").strip() or (r.stderr or "").strip()


print("[inject]", run(["inject", str(pid)])[:80])

st1 = run(["status", str(pid)])
print("[status-1]", st1)
assert "revision=1" in st1 and "F_A1=5.00" in st1, "注入初始状态不对"

print("[setspeed]", run(["setspeed", "6.5", str(pid)]))
st2 = run(["status", str(pid)])
print("[status-2]", st2)
assert "revision=2" in st2 and "F_A1=6.50" in st2, "revision 未续接（bug）"

print("[eject]", run(["eject", str(pid)]))
time.sleep(1.5)
alive = any(p["pid"] == pid for p in osd.list_all_processes())
print("[eject 后] notepad 存活:", alive, "（应存活 = 自卸载未崩溃）")
print("SMOKE_OK")
