# -*- coding: utf-8 -*-
"""M1 自检诊断：确认 hook DLL 是否真的加载 + 真的生效。

判定链（每一环都可证伪）：
  1. 模块是否出现在进程模块列表（Toolhelp32，比 tasklist /M 可靠）；
  2. 共享内存 sentinel 是否为 0x424F4F53（DLL 跑起来了）；
  3. heartbeat 是否递增（监视线程活着，没卡死）；
  4. status 是否为 1 = HOOK_STATUS_OK（kernel32 hook 全装上了）；
  5. probeRatio 是否 ≈ 请求倍率（DLL 内部自测：hook 真的改变了时钟）。
"""
import ctypes
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "backend"))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOOSTER = os.path.join(ROOT, "tools", "booster", "booster.exe")
TOOLS = os.path.join(ROOT, "tools", "booster")

k32 = ctypes.windll.kernel32
k32.OpenProcess.restype = ctypes.c_void_p
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", ctypes.c_ulong), ("th32ModuleID", ctypes.c_ulong),
                ("th32ProcessID", ctypes.c_ulong), ("GlblcntUsage", ctypes.c_ulong),
                ("ProccntUsage", ctypes.c_ulong),
                ("modBaseAddr", ctypes.c_void_p), ("modBaseSize", ctypes.c_ulong),
                ("hModule", ctypes.c_void_p),
                ("szModule", ctypes.c_wchar * 256),
                ("szExePath", ctypes.c_wchar * 260)]


def modules_of(pid):
    """用 Toolhelp32 枚举模块（tasklist /M 对某些进程/位数会看不到）。"""
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
    if snap == ctypes.c_void_p(-1).value or not snap:
        return None
    out = []
    me = MODULEENTRY32W()
    me.dwSize = ctypes.sizeof(me)
    ok = k32.Module32FirstW(ctypes.c_void_p(snap), ctypes.byref(me))
    while ok:
        out.append(me.szModule)
        ok = k32.Module32NextW(ctypes.c_void_p(snap), ctypes.byref(me))
    k32.CloseHandle(ctypes.c_void_p(snap))
    return out


def find_notepad_pids():
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq notepad.exe",
                        "/FO", "CSV", "/NH"],
                       capture_output=True, encoding="mbcs", errors="replace")
    pids = []
    for line in (r.stdout or "").splitlines():
        parts = [p.strip('"') for p in line.split(",")]
        if len(parts) >= 2 and parts[0].lower() == "notepad.exe":
            try:
                pids.append(int(parts[1]))
            except ValueError:
                pass
    return pids


def run(cmd):
    r = subprocess.run([BOOSTER] + cmd, capture_output=True, timeout=20,
                       encoding="mbcs", errors="replace")
    return ((r.stdout or "") + (r.stderr or "")).strip()


def parse_status(text):
    """把 booster status 的两行文本解析成 dict。"""
    d = {}
    for tok in text.replace("\n", " ").replace("DLL:", " ").split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            d[k] = v
    return d


def main():
    # 用 64 位靶子（hook64.dll），避开 32/64 位数混淆
    subprocess.Popen(["notepad.exe"])
    time.sleep(2)
    pids = find_notepad_pids()
    if not pids:
        print("FAIL: notepad 未启动")
        return 1
    pid = pids[0]
    print(f"[target] notepad pid={pid} (x64 → hook64.dll)")

    print(f"[inject] {run(['inject', str(pid)])[:100]}")
    time.sleep(0.5)

    mods = modules_of(pid)
    if mods is None:
        print("[modules] 快照失败（权限？）")
    else:
        hits = [m for m in mods if "hook" in m.lower()]
        print(f"[modules] 共 {len(mods)} 个；hook 相关: {hits or '（无）'}")

    prev_hb = None
    verdict = {}
    for i in range(10):
        st = parse_status(run(["status", str(pid)]))
        hb = st.get("heartbeat", "0")
        print(f"[t={i*0.6:.1f}s] status={st.get('status')} stage2={st.get('stage2State')} "
              f"winmm={st.get('winmmOk')} sentinel={st.get('sentinel')} hb={hb} "
              f"refresh={st.get('refreshCount')} probeRatio={st.get('probeRatio')} "
              f"probeMs={st.get('probeRealMs')} qpcF={st.get('qpcFactor')} "
              f"actualA1={st.get('actualA1')}")
        verdict = st
        prev_hb = hb
        time.sleep(0.6)

    print("\n===== 判定 =====")
    st = verdict
    print("1. DLL 在模块列表      :",
          "是" if mods and any("hook" in m.lower() for m in mods) else "否")
    print("2. sentinel == 424f4f53 :", st.get("sentinel"))
    print("3. heartbeat 递增       :", prev_hb)
    print("4. status == 1 (OK)     :", st.get("status"))
    print("5. refreshCount > 0     :", st.get("refreshCount"))
    print("6. qpcFactor ≈ 请求倍率  :", st.get("qpcFactor"))
    print("7. probeRatio ≈ 倍率     :", st.get("probeRatio"),
          f"(probeRealMs={st.get('probeRealMs')})")

    print("\n[cleanup] eject")
    print(run(["eject", str(pid)]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
