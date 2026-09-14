# -*- coding: utf-8 -*-
"""按进程输出百度客户端的网络读速率，判断下载流量落在哪个进程。

用法：python scripts\\_diag_client_rate.py [--seconds 20]
"""
import argparse
import ctypes
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "backend"))
from accel import openspeedy as osd  # noqa: E402

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_k32 = ctypes.windll.kernel32
_k32.OpenProcess.restype = ctypes.c_void_p


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong)]


def io_of(pid):
    h = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        c = IO_COUNTERS()
        if not _k32.GetProcessIoCounters(ctypes.c_void_p(h), ctypes.byref(c)):
            return None
        return int(c.ReadTransferCount), int(c.WriteTransferCount)
    finally:
        _k32.CloseHandle(ctypes.c_void_p(h))


def fmt(bps):
    return f"{bps/1e6:.2f}MB/s" if bps >= 1e6 else f"{bps/1e3:.0f}KB/s"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=20)
    a = ap.parse_args()

    procs = {p["pid"]: p["name"] for p in osd.list_baidu_processes()}
    if not procs:
        print("客户端未运行")
        return 1
    prev = {pid: io_of(pid) for pid in procs}
    prev_t = time.time()
    acc = {pid: [0, 0] for pid in procs}
    t0 = time.time()
    while time.time() - t0 < a.seconds:
        time.sleep(1.0)
        now = time.time()
        dt = now - prev_t
        for pid in list(prev):
            cur = io_of(pid)
            if cur is None or prev[pid] is None:
                continue
            acc[pid][0] += max(0, cur[0] - prev[pid][0])
            acc[pid][1] += max(0, cur[1] - prev[pid][1])
            prev[pid] = cur
        prev_t = now
    dur = time.time() - t0
    print(f"=== 各进程速率（{dur:.0f}s 平均）===")
    rows = sorted(acc.items(), key=lambda kv: -kv[1][0])
    for pid, (r, w) in rows:
        if r or w:
            print(f"  pid={pid:<8} {procs[pid]:<26} 读 {fmt(r/dur):>10}  写 {fmt(w/dur):>10}"
                  f"  累计读 {r/1e6:.1f}MB")
    tot = sum(v[0] for v in acc.values())
    print(f"  合计读 {fmt(tot/dur)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
