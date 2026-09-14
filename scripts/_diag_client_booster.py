# -*- coding: utf-8 -*-
"""M1-b：真实百度客户端速率对照实验（核心赌注 = 选择性 hook 是否还衰减）。

用法（每段跑一次，段间由人工/上游脚本切换倍率）：

  python scripts\\_diag_client_booster.py check
      # 客户端是否在跑、哪个 pid 在下载、当前速率（>50KB/s 才算"有下载"）

  python scripts\\_diag_client_booster.py run --label baseline --seconds 120
  python scripts\\_diag_client_booster.py run --label F5 --inject --factor 5 --seconds 600
  python scripts\\_diag_client_booster.py run --label F10 --factor 10 --probe off --seconds 600
  python scripts\\_diag_client_booster.py run --label post --factor 1 --probe off --seconds 120

  python scripts\\_diag_client_booster.py report backend/data/client_ab.csv

判定口径（抗抖动：只用「累计读字节在固定窗口内的增量」，逐样本速率是分片方波）：
  首 60s 窗口 / 末 60s 窗口各算一次「引擎进程累计读字节的增量」，比值 ≥70% 稳住、
  45–70% 部分衰减、<45% 衰减。窗口长度固定为秒数（--window，默认 60），
  这样不同时长的段算的是同一个物理窗口、段间可比。
  基线段（无 hook）与 F=1 段一起用来区分三件事：客户端自身衰减 / DLL 开销 / hook 效果。

测量不变量（每 5s 校验一次，违反的样本标 invalid；通过率 <90% 的段整段作废）：
  1) qpcFactor ≈ 请求倍率     → hook 真的生效了（不是"装上了但因子 1.0"）
  2) refreshCount ≥ 1         → 根因 1（revision 被提前消费）没有复发
  3) 0.4×F ≤ probeRatio ≤ 2.5×F → 自测窗口没被 B 组缩放污染（缩放了会变 F^2）
  4) groupMask/F_B == 1.0     → B 组透传（选择性 hook 的前提）
  5) hook32.dll 必须在位，且与"本段声明是否注入"一致；speedpatch32.dll 必须不在位

⚠ "hook 到底在不在"只信模块枚举（hook_state），不信 booster status：
  eject 之后 status 会重新打开同名共享内存映射、打印卸载前的旧值（交接文档 4.3-3），
  所以基线段靠 status 判定自己"干净"会得到假数据——client_ab.csv 的第一行
  baseline 段就带着 qpcFactor=5.001/refreshCount=7，就是被这个坑污染的。
"""
import argparse
import csv
import ctypes
import os
import statistics
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "backend"))
from accel import openspeedy as osd  # noqa: E402
from accel.booster import PROBE_OFF  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOOSTER = os.path.join(ROOT, "tools", "booster", "booster.exe")
CLIENT = r"D:\A2000-soft\百度网盘\BaiduNetdisk\BaiduNetdisk.exe"
CSV_DEFAULT = os.path.join(ROOT, "backend", "data", "client_ab.csv")

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
    """(ReadTransferCount, WriteTransferCount)；进程不在返回 None。"""
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


TH32CS_SNAPMODULE = 0x8
TH32CS_SNAPMODULE32 = 0x10


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", ctypes.c_ulong), ("th32ModuleID", ctypes.c_ulong),
                ("th32ProcessID", ctypes.c_ulong), ("GlblcntUsage", ctypes.c_ulong),
                ("ProccntUsage", ctypes.c_ulong), ("modBaseAddr", ctypes.c_void_p),
                ("modBaseSize", ctypes.c_ulong), ("hModule", ctypes.c_void_p),
                ("szModule", ctypes.c_wchar * 256),
                ("szExePath", ctypes.c_wchar * 260)]

_k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p


def mods(pid):
    """枚举进程已加载模块名（跨 WOW64：SNAPMODULE|SNAPMODULE32）。

    这是判定"hook 到底在不在"的唯一可信来源——booster status 在 eject 之后
    仍会打印旧值（交接文档 4.3-3）。沿用 _diag_client_procs.py 的已实测写法。
    """
    for flags in (TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, TH32CS_SNAPMODULE32):
        s = _k32.CreateToolhelp32Snapshot(flags, pid)
        if not s or s == ctypes.c_void_p(-1).value:
            continue
        out = []
        me = MODULEENTRY32W()
        me.dwSize = ctypes.sizeof(me)
        ok = _k32.Module32FirstW(ctypes.c_void_p(s), ctypes.byref(me))
        while ok:
            out.append(me.szModule)
            ok = _k32.Module32NextW(ctypes.c_void_p(s), ctypes.byref(me))
        _k32.CloseHandle(ctypes.c_void_p(s))
        if out:
            return out
    return []


def hook_state(pid):
    """(hook 在位, speedpatch 在位)。speedpatch = OpenSpeedy 残留，两套 MinHook 冲突。"""
    names = [m.lower() for m in mods(pid)]
    return (any("hook32" in m or "hook64" in m for m in names),
            any("speedpatch" in m for m in names))


def window_bytes(cum, a, b):
    """累计读数序列 cum 上，第 a~b 号样本之间真正传了多少字节。

    cum[0] 是段起始（t0）的累计值，cum[i] 是第 i 个样本结束时的累计值，
    所以 cum[b] - cum[a] 就是 (a, b] 窗口内的真增量。
    旧版在这里对 a == 0 直接返回 cum[b-1]，等于把"进程自启动以来的全部
    读字节"当成首窗口增量——段间因此不可比，而且偏差方向随进程年龄翻转。
    """
    if b <= a or a < 0 or b >= len(cum):
        return 0
    return max(0, cum[b] - cum[a])


_k32.GetCompressedFileSizeW.restype = ctypes.c_ulong
_k32.GetCompressedFileSizeW.argtypes = [ctypes.c_wchar_p,
                                        ctypes.POINTER(ctypes.c_ulong)]


def allocated_size(path):
    """文件在磁盘上真实占用的字节数（对稀疏/预分配文件 = 已写入量）。

    .downloading 分片是被预分配成整文件大小的，看 st_size 永远是 3.3G；
    GetCompressedFileSizeW 返回的是实际分配量，才是真实下载进度。
    这是"到底下了多少"的地面真值——GetProcessIoCounters 的 ReadTransferCount
    把本地文件读和网络读混在一起（实测出现过 260MB 只读不写的突发，
    那是本地校验读，不是下载）。
    """
    if not path or not os.path.exists(path):
        return None
    hi = ctypes.c_ulong(0)
    lo = _k32.GetCompressedFileSizeW(path, ctypes.byref(hi))
    if lo == 0xFFFFFFFF and _k32.GetLastError() != 0:
        return None
    return (hi.value << 32) | lo


# ---------------------------------------------------------------- 网卡收包（第三信号）
# 实现放在 backend/accel/netmon.py，与 M2 闭环共用同一份代码（避免两处实现漂移）。
# 为什么需要它：GetProcessIoCounters 把网络读与本地文件读混算，落盘量又会被
# 预分配/秒传抬高，两个指标实测能差 9.6~40 倍；网卡收包是系统级真值。
from accel import netmon as _netmon  # noqa: E402

_if_rows = _netmon.if_rows
NicRxCounter = _netmon.NicRxCounter


def booster(*cmd):
    r = subprocess.run([BOOSTER] + [str(c) for c in cmd], capture_output=True,
                       timeout=25, encoding="mbcs", errors="replace")
    return ((r.stdout or "") + (r.stderr or "")).strip()


def parse_status(text):
    d = {}
    for tok in text.replace("\n", " ").replace("DLL:", " ").split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            d[k] = v
    return d


def check_invariants(st, want_factor, probe_enabled=True, hook_present=None,
                     speedpatch=False, expect_hook=True, hook_alive=None):
    """返回 (ok, 问题列表)。st 是 booster status 解析出的 dict。

    hook_present 来自模块枚举；hook_alive 来自"心跳是否还在走"。
    两者都要：eject 之后 hook32.dll 可能**残留在模块列表里但不卸载**
    （实测 113300：uninstall=1、心跳冻结、模块仍在位），只看模块会误判成
    "hook 还在"；反过来只看 status 会被 eject 后的旧值骗。
    """
    bad = []
    if speedpatch:
        bad.append("speedpatch 在位（OpenSpeedy 残留，两套 MinHook 冲突）")
    if expect_hook:
        if hook_present is False:
            bad.append("hook32.dll 不在位（status 可能是 eject 后的旧值）")
        elif hook_alive is False:
            bad.append("hook32.dll 在位但心跳已停：DLL 已死，加速未生效")
    else:
        if hook_alive:
            bad.append("本段声明无 hook，但 hook DLL 仍在运行（对照被污染）")
        elif hook_present:
            bad.append("hook32.dll 残留模块且心跳已停：加速已关，但模块没卸载；"
                       "建议重启客户端拿到真正干净的对照")
    if not expect_hook:
        return (not bad), bad      # 无 hook 段没有可采信的 DLL 自检字段
    try:
        qpc = float(st.get("qpcFactor", 0))
        f_a1 = float(st.get("F_A1", 0))
        f_b = float(st.get("F_B", 0))
        mask = int(float(st.get("mask", 0)))
        refresh = int(float(st.get("refreshCount", 0)))
        probe = float(st.get("probeRatio", 0))
        status = int(float(st.get("status", 0)))
        sentinel = st.get("sentinel", "0")
    except ValueError:
        return False, ["status 字段解析失败"]

    if sentinel != "424f4f53":
        bad.append(f"sentinel={sentinel}（DLL 没跑起来）")
    if status != 1:
        bad.append(f"status={status}（hook 未生效）")
    if refresh < 1:
        bad.append(f"refreshCount={refresh}（根因 1 复发：因子会停在 1.0）")
    # hook 生效性：qpcFactor 必须等于请求倍率
    if abs(qpc - want_factor) > 0.05:
        bad.append(f"qpcFactor={qpc} ≠ 请求 {want_factor}")
    if abs(f_a1 - want_factor) > 0.05:
        bad.append(f"F_A1={f_a1} ≠ 请求 {want_factor}")
    # B 组透传
    if abs(f_b - 1.0) > 1e-6:
        bad.append(f"F_B={f_b}（应为 1.0，否则选择性 hook 退化成 OpenSpeedy）")
    if not (mask & 4):
        bad.append(f"mask={mask} 未含 GROUP_B（B 摘出去会让 A2 也按 1.0 走）")
    # 自测窗口未被 B 缩放污染
    if probe_enabled and refresh >= 1:
        lo, hi = 0.4 * want_factor, 2.5 * want_factor
        if not (lo <= probe <= hi):
            bad.append(f"probeRatio={probe} 不在 [{lo:.2f},{hi:.2f}]"
                       f"（B 被缩放时 probe 会变成 F^2）")
    return (not bad), bad


def baidu_procs():
    return osd.list_baidu_processes()


def engine_pid(procs=None):
    """找出真正承载下载引擎的进程。

    实测（2026-09-13）：下载流量全在 baidunetdiskhost.exe（加载了
    kernel.dll 的 qingluan 下载模块 + netbase.dll），而 BaiduNetdisk.exe
    只是壳，读速率长期 0。只注入壳进程 = 什么都没提速。
    判定方式：白名单里 ReadTransferCount 最大的那个。
    """
    procs = procs if procs is not None else baidu_procs()
    best, best_bytes = None, -1
    for p in procs:
        if p["name"].lower() != "baidunetdiskhost.exe":
            continue
        io = io_of(p["pid"])
        if io and io[0] > best_bytes:
            best, best_bytes = p["pid"], io[0]
    if best is None:
        for p in procs:
            if p["name"].lower() == "baidunetdisk.exe":
                best = p["pid"]
                break
    return best


def is_wow64(pid):
    h = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        w = ctypes.c_int(0)
        if _k32.IsWow64Process(ctypes.c_void_p(h), ctypes.byref(w)):
            return bool(w.value)
        return None
    finally:
        _k32.CloseHandle(ctypes.c_void_p(h))


def fmt(bps):
    if bps >= 1e6:
        return f"{bps/1e6:.2f}MB/s"
    return f"{bps/1e3:.0f}KB/s"


# ------------------------------------------------------------------ check
def cmd_check(_args):
    procs = baidu_procs()
    print(f"[进程] {len(procs)} 个百度相关进程")
    for p in procs:
        io = io_of(p["pid"])
        r = f"{io[0]/1e6:9.1f}MB" if io else "        ?"
        hp, sp_ = hook_state(p["pid"])
        print(f"   pid={p['pid']:<8} {p['name']:<26} WOW64={is_wow64(p['pid'])!s:<5}"
              f" 累计读={r} hook={'Y' if hp else 'N'}"
              f"{' speedpatch!' if sp_ else ''}")
    if not procs:
        print("!! 客户端未运行")
        print(f"   启动：start \"\" \"{CLIENT}\"")
        return 1
    # 孤儿宿主：客户端主进程退了，但 baidunetdiskhost.exe 还活着。
    # 它会一直占着 hook32.dll → native 重编 LNK1104，eject 也卸不干净
    # （实测踩到：客户端已关，pid 113300 仍持着 hook32.dll，直接卡死重编）。
    main_alive = any(p["name"].lower() == "baidunetdisk.exe" for p in procs)
    orphans = [p["pid"] for p in procs
               if p["name"].lower() == "baidunetdiskhost.exe"
               and hook_state(p["pid"])[0]]
    if not main_alive and orphans:
        print(f"\n!! 孤儿引擎宿主：客户端主进程已退出，但 pid {orphans} 还活着并"
              f"持有 hook32.dll")
        print("   先 taskkill 掉它们，否则 native\\build.bat 会 LNK1104、"
              "eject 也卸不干净")
    ep = engine_pid(procs)
    print(f"\n[下载引擎进程] pid={ep}（{dict((p['pid'], p['name']) for p in procs).get(ep)}）"
          f" ← 要注入的是这个，不是 BaiduNetdisk.exe")
    # 引擎判定证据要用模块口径（进程名口径会把插件宿主也算成引擎）
    for p in procs:
        if p["name"].lower() == "baidunetdiskhost.exe":
            mm = mods(p["pid"])
            print(f"   host pid={p['pid']} 模块数={len(mm)} "
                  f"kernel.dll={'kernel.dll' in mm} "
                  f"vastplayer={'vastplayer.dll' in mm}"
                  f"{'  <- 真引擎' if 'kernel.dll' in mm else '  <- 插件宿主，注了也没用'}")
    pids = [p["pid"] for p in procs]
    s = osd.SpeedSampler()
    nic = NicRxCounter()
    nic_ok = nic.pin() is not None
    s.sample(pids)
    if nic_ok:
        nic.sample()
    time.sleep(6)
    sp = s.sample(pids)
    nps = (nic.sample() or 0) / 6.0 if nic_ok else 0.0
    print(f"[总速率] 读计数 {fmt(sp)} | 网卡 {fmt(nps)}"
          + (f"（接口 idx={nic.pinned_index}，系统级，绝对值约偏高 25%）"
             if nic_ok else "（网卡不可用，只用读计数）"))
    # 就绪闸门以网卡为准：读计数会被本地文件读抬高，实测空载时也能报 469KB/s
    # 而网卡只有 11KB/s——只看读计数会误报"有下载在进行"。
    active = nps if nic_ok else sp
    thresh = 500 * 1024 if nic_ok else 50 * 1024
    if ep:
        print(f"\n[自检] booster status {ep}:\n{booster('status', ep)}")
    if active < thresh:
        print(f"\n!! 没有明显下载活动（判据：{'网卡' if nic_ok else '读计数'} "
              f"{fmt(active)} < {fmt(thresh)}）：先在客户端里继续一个未完成的下载再跑 run")
        return 1
    print("\nOK：有下载在进行，可以开始 run 各段")
    return 0


# ------------------------------------------------------------------ run
def cmd_run(args):
    procs = baidu_procs()
    if not procs:
        print("!! 客户端未运行（先跑 check）")
        return 1
    pids = [p["pid"] for p in procs]
    ep = engine_pid(procs)
    print(f"[进程] {len(pids)} 个；下载引擎 pid={ep}")
    print(f"[位数] 引擎 WOW64={is_wow64(ep)}（True → hook32.dll）")
    target = int(args.pid) if args.pid else ep
    if not target:
        print("!! 找不到下载引擎进程")
        return 1

    if args.inject:
        print(f"[inject {target}] {booster('inject', target)}")
        time.sleep(1.0)
    if args.factor is not None:
        print(f"[setspeed {args.factor} {target}] "
              f"{booster('setspeed', args.factor, target)}")
    if args.probe is not None:
        print(f"[probe {args.probe} {target}] {booster('probe', args.probe, target)}")
    if args.eject:
        print(f"[eject {target}] {booster('eject', target)}")
        time.sleep(1.0)
    time.sleep(0.5)

    probe_enabled = (str(args.probe).lower() != "off")
    want_factor = args.factor if args.factor is not None else 5.0
    # 本段是否声明"应当有 hook"。--expect-hook 可显式覆盖推断。
    if args.expect_hook == "off":
        hooks_expected = False
    elif args.expect_hook == "on":
        hooks_expected = True
    else:
        hooks_expected = bool(args.inject or args.factor is not None or args.pid)
        if args.eject:
            hooks_expected = False

    hook_present, speedpatch = hook_state(target)
    print(f"[模块] {target}: hook32.dll {'在位' if hook_present else '不在位'} / "
          f"speedpatch {'在位（冲突！）' if speedpatch else '不在位'} "
          f"（本段声明：{'应有 hook' if hooks_expected else '应无 hook'}）")
    if args.eject:
        print("[eject] status 在 eject 后为旧值，本段不采信其自检字段")
        st = {}
    else:
        st = parse_status(booster("status", target))
        print(f"[自检] status={st.get('status')} stage2={st.get('stage2State')} "
              f"winmmOk={st.get('winmmOk')} sentinel={st.get('sentinel')} "
              f"refreshCount={st.get('refreshCount')} probeRatio={st.get('probeRatio')} "
              f"qpcFactor={st.get('qpcFactor')} actualA1={st.get('actualA1')}")
    if not hooks_expected:
        print("[基线] 声明无 hook：靠模块枚举（不是 status）证明本段干净")

    path = args.csv or CSV_DEFAULT
    new = not os.path.exists(path)
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    f = open(path, "a", newline="", encoding="utf-8-sig")
    w = csv.writer(f)
    if new:
        w.writerow(["host_t", "label", "factor", "t", "total_bps",
                    "probeRatio", "qpcFactor", "refreshCount", "heartbeat",
                    "pids_read_mb", "pids_write_mb", "inv_ok", "inv_problem",
                    "engine_pid", "hook_present", "speedpatch_present",
                    "cum_engine_mb", "stalled", "disk_mb", "nic_mb"])

    t0 = time.time()
    prev = {p: io_of(p) for p in pids}
    prev_t = t0
    samples = []
    inv_ok_samples = []
    stalled_flags = []
    last_status = 0.0
    inv_ok, inv_bad = True, []
    pid_drift = False
    gone_sec = 0
    prev_hb = None
    hook_alive = None
    last_reassert = 0.0
    cum0 = io_of(target)
    if cum0 is None:
        print(f"!! 读不到引擎进程 {target} 的 IO 计数（pid 可能已退出），本段取消")
        f.close()
        return 1
    # cum[i] = 第 i 个样本结束时引擎进程的累计读字节；cum[0] = t0 基线。
    # 首窗口必须从 cum[0] 做差——旧版对 a==0 直接返回 cum[b-1]，等于把
    # "进程自启动以来的全部读字节"当成首窗口增量，段间因此不可比。
    cum = [cum0[0]]
    # 目标分片的"已落盘字节"曲线：下载进度的地面真值，与读计数互相印证
    d0 = allocated_size(args.progress_file)
    disk = [d0]
    pf_total = None
    if args.progress_file and os.path.exists(args.progress_file):
        pf_total = os.path.getsize(args.progress_file)
    file_done = False
    if args.progress_file:
        if d0 is None:
            print(f"[进度] 读不到 {args.progress_file} 的落盘量，本段只用读计数判据")
        else:
            print(f"[进度] 起始已落盘 {d0/1e6:.1f}MB / 总 {pf_total/1e6:.1f}MB")
    # 网卡收包（第三信号）：系统级网络真值，不受进程 I/O 记账口径影响。
    # 含非百度流量，所以是"网络收包上界"；机器闲置时≈下载量。
    nic_counter = None if args.no_nic else NicRxCounter()
    nic = [0]
    if nic_counter is not None:
        idx = nic_counter.pin()
        if idx is None:
            nic_counter = None
            print("[网卡] GetIfTable 不可用，本段没有网络地面真值")
        else:
            print(f"[网卡] 已钉住接口 idx={idx}（系统级收包量，含非百度流量；"
                  f"绝对值约偏高 25%，只用于首/末窗口比值）")

    try:
        while time.time() - t0 < args.seconds:
            time.sleep(1.0)
            now = time.time()
            dt = now - prev_t
            total = 0
            reads, writes = [], []
            for pid in list(prev.keys()):
                cur = io_of(pid)
                if cur is None or prev[pid] is None:
                    continue
                dr = cur[0] - prev[pid][0]
                dw = cur[1] - prev[pid][1]
                if dr >= 0:
                    total += dr
                reads.append(cur[0] / 1e6)
                writes.append(cur[1] / 1e6)
                prev[pid] = cur
            prev_t = now
            bps = max(0.0, total / dt) if dt > 0 else 0.0
            samples.append(bps)
            stalled_flags.append(1 if total < 1024 else 0)

            io_ep = io_of(target)
            if io_ep is None:
                gone_sec += 1
                cum.append(cum[-1])
            else:
                cum.append(io_ep[0])
            d_cur = allocated_size(args.progress_file) if args.progress_file else None
            disk.append(d_cur if d_cur is not None
                        else (disk[-1] if disk else None))
            if nic_counter is not None:
                v = nic_counter.sample()
                nic.append(v if v is not None else nic[-1])
            if pf_total and disk[-1] is not None and disk[-1] >= pf_total - 1e6:
                if not file_done:
                    file_done = True
                    print(f"  !! 目标文件已下完（落盘 {disk[-1]/1e6:.0f}MB / "
                          f"总 {pf_total/1e6:.0f}MB）：末窗口不再反映下载速率")
            elif pf_total and not os.path.exists(args.progress_file):
                # 客户端下载完成后会把 .downloading **重命名**掉，于是落盘量读数
                # 直接消失、曲线走平 —— 看起来像"衰减到 0"，其实只是没东西可下了。
                # 必须在"曾经存在过"（pf_total 非空）的前提下判，避免路径写错就误报。
                if not file_done:
                    file_done = True
                    print("  !! 目标分片已消失（下载完成被重命名）："
                          "末窗口不再反映下载速率")

            if now - last_status >= 5.0:
                last_status = now
                if not args.eject:
                    st = parse_status(booster("status", target))
                hook_present, speedpatch = hook_state(target)
                # 心跳是否还在走：区分"DLL 生效中"与"模块残留但 DLL 已死"
                hb_now = st.get("heartbeat") if st else None
                hook_alive = (hb_now is not None and prev_hb is not None
                              and hb_now != prev_hb)
                prev_hb = hb_now
                # 重申倍率：页面/后端随时可能改写共享内存（实测被后端注入覆盖成 5.0，
                # 整段因此作废）。测量期间必须由本工具掌握倍率这个变量。
                if args.hold_factor and args.factor is not None:
                    if now - last_reassert >= 15.0:
                        last_reassert = now
                        booster('setspeed', args.factor, target)
                inv_ok, inv_bad = check_invariants(
                    st, want_factor, probe_enabled, hook_present, speedpatch,
                    hooks_expected, hook_alive)
                if not inv_ok:
                    print(f"  !! 不变量违反：{'; '.join(inv_bad)}")
                cur_ep = engine_pid()
                if cur_ep and cur_ep != target and not pid_drift:
                    pid_drift = True
                    print(f"  !! 引擎进程已变更：{target} → {cur_ep}"
                          f"（本段作废：旧 pid 计数会走平，会被误判成衰减）")
            inv_ok_samples.append(inv_ok and len(samples) > 5)

            w.writerow([time.strftime("%H:%M:%S"), args.label,
                        args.factor if args.factor is not None else "",
                        round(now - t0, 1), round(bps), st.get("probeRatio", ""),
                        st.get("qpcFactor", ""), st.get("refreshCount", ""),
                        st.get("heartbeat", ""),
                        round(sum(reads), 1), round(sum(writes), 1),
                        1 if inv_ok else 0, "; ".join(inv_bad),
                        target, 1 if hook_present else 0,
                        1 if speedpatch else 0,
                        round((cum[-1] - cum[0]) / 1e6, 2),
                        stalled_flags[-1],
                        (round((disk[-1] - disk[0]) / 1e6, 2)
                         if disk and disk[-1] is not None and disk[0] is not None
                         else ""),
                        round((nic[-1] - nic[0]) / 1e6, 2) if nic else ""])
            f.flush()
            el = now - t0
            if int(el) % 15 == 0:
                print(f"  t={el:5.0f}s {fmt(bps):>10}  probe={st.get('probeRatio')} "
                      f"qpcF={st.get('qpcFactor')} refresh={st.get('refreshCount')} "
                      f"hb={st.get('heartbeat')} hook={'Y' if hook_present else 'N'} "
                      f"inv={'OK' if inv_ok else 'BAD'}")
    except KeyboardInterrupt:
        print("\n[中断] 提前结束")
    finally:
        f.close()

    valid_frac = (sum(1 for v in inv_ok_samples if v) / len(inv_ok_samples)
                  if inv_ok_samples else 0.0)
    if not samples:
        print("没有采到样本")
        return 1
    n = len(samples)
    third = max(1, n // 3)
    head = statistics.mean(samples[:third])
    tail = statistics.mean(samples[-third:])
    mid = statistics.mean(samples[third:-third]) if n > 2 * third else tail

    # 抗抖动的衰减判据：用「累计字节曲线」而不是逐样本速率。
    # 客户端下载是分片串行（一块吃完换下一块），逐样本速率天然是
    # 4MB/s ↔ 10KB/s 的方波，用速率做前后段对比必然误判。
    # 累计字节在任一窗口内的增量与瞬时抖动无关；cum[0] 是 t0 基线，
    # 所以首窗口也是真增量（旧版对 a==0 直接返回 cum[b-1]，首窗口等于
    # 进程自启动以来的全部读字节，段间不可比）。
    win = max(1, min(args.window, n))
    early_bps = window_bytes(cum, 0, win) / win
    late_bps = window_bytes(cum, n - win, n) / win
    decay_pct = (late_bps / early_bps * 100) if early_bps > 0 else 0.0

    # 落盘字节判据
    d_early = d_late = d_pct = None
    if disk and disk[0] is not None:
        d_early = window_bytes(disk, 0, win) / win
        d_late = window_bytes(disk, n - win, n) / win
        d_pct = (d_late / d_early * 100) if d_early > 0 else 0.0

    # 网卡收包判据：三指标里最可信（系统级，不受预分配/本地读影响）
    n_early = n_late = n_pct = None
    if nic and len(nic) > 1 and (nic[-1] - nic[0]) > 0:
        n_early = window_bytes(nic, 0, win) / win
        n_late = window_bytes(nic, n - win, n) / win
        n_pct = (n_late / n_early * 100) if n_early > 0 else 0.0

    # 零增长降级（必须在打印之前做，否则打印与判定会对不上）
    if d_pct is not None and d_early is not None and d_early <= 0:
        d_pct = None
        d_zero = True
    else:
        d_zero = False
    if n_pct is not None and n_early is not None and n_early <= 0:
        n_pct = None
        n_zero = True
    else:
        n_zero = False

    # 停摆统计：塌陷最早的信号是「停摆间隔逐渐拉长」，比总字节更早可见
    stall_sec = sum(stalled_flags)
    stall_frac = stall_sec / n
    longest = cur_run = 0
    for s in stalled_flags:
        cur_run = cur_run + 1 if s else 0
        longest = max(longest, cur_run)
    half = max(1, n // 2)
    sf1 = sum(stalled_flags[:half]) / half
    sf2 = sum(stalled_flags[half:]) / max(1, n - half)
    active = [b for b in samples if b >= 1024]
    active_bps = statistics.mean(active) if active else 0.0

    print(f"\n===== {args.label} 段汇总（{n} 样本 / {args.seconds}s）=====")
    print(f"  不变量通过率 {valid_frac*100:.0f}%  hook={int(hook_present)}"
          f"  speedpatch={int(speedpatch)}  引擎 pid={target}"
          f"  本段声明={'应有 hook' if hooks_expected else '应无 hook'}")
    print(f"  瞬时速率：均值 {fmt(statistics.mean(samples))}  中位 {fmt(statistics.median(samples))}"
          f"  最小 {fmt(min(samples))}  最大 {fmt(max(samples))}")
    print(f"  瞬时前1/3 {fmt(head)}  中1/3 {fmt(mid)}  后1/3 {fmt(tail)}（受分片方波影响大）")
    if n_pct is not None:
        print(f"  网卡收包窗口：首 {win}s {fmt(n_early)} -> 末 {win}s {fmt(n_late)}"
              f"（{n_pct:.0f}%）<- 主判据（系统级网络真值，含非百度流量）")
    elif n_zero:
        print("  [!] 网卡收包在首窗口内零增长，网络判据不可用")
    print(f"  读计数窗口：  首 {win}s {fmt(early_bps)} -> 末 {win}s {fmt(late_bps)}"
          f"（{decay_pct:.0f}%）<- 含本地读，会偏高")
    if d_pct is not None:
        print(f"  落盘字节窗口：首 {win}s {fmt(d_early)} -> 末 {win}s {fmt(d_late)}"
              f"（{d_pct:.0f}%）<- 目标文件真实增长（可能含预分配）")
    elif d_zero:
        print("  [!] 落盘量在首窗口内零增长（文件没在写？），落盘判据不可用")
    print(f"  停摆：占比 {stall_frac*100:.0f}%（{stall_sec}s）  最长连续 {longest}s"
          f"  前半 {sf1*100:.0f}% -> 后半 {sf2*100:.0f}%"
          f"{'  [停摆变多：塌陷早期信号]' if sf2 - sf1 > 0.15 else ''}")
    print(f"  仅在传输的秒里平均 {fmt(active_bps)}  段内总传输 "
          f"{(cum[-1] - cum[0])/1e6:.1f}MB")
    if n < 2 * args.window:
        print(f"  !! 本段只有 {n}s，不足 2 倍窗口（{args.window}s），前后窗口重叠")

    # 信任门槛：任一不满足 -> 本段不作为衰减证据
    problems = []
    if valid_frac <= 0.9:
        problems.append(f"不变量通过率 {valid_frac*100:.0f}% <=90%")
    if pid_drift:
        problems.append("引擎进程中途变更")
    if gone_sec:
        problems.append(f"引擎进程失联 {gone_sec}s")
    if speedpatch:
        problems.append("speedpatch 在位")
    if hook_present != hooks_expected:
        problems.append(f"hook 在位({int(hook_present)}) 与声明"
                        f"({int(hooks_expected)}) 不符")
    if stall_frac > args.stall_gate:
        problems.append(f"停摆占比 {stall_frac*100:.0f}% >{args.stall_gate*100:.0f}%")
    elif stall_frac > args.stall_warn:
        print(f"  [!] 停摆占比 {stall_frac*100:.0f}% 超过警戒线 "
              f"{args.stall_warn*100:.0f}%，判据仅作趋势参考")
    if file_done:
        problems.append("目标文件在本段内下完（末窗口不再反映下载速率）")
    # 两个指标必须互相印证：读计数（进程网络/文件读）与落盘量（目标文件增长）
    # normal 时两者同量级。差 3 倍以上就说明其中一个在说谎，此时两个都不能采信：
    #   - 落盘 >> 读计数：客户端在预分配/本地秒传（实测 S4 出现 40 倍差，
    #     全进程只读 0.28MB/s 而文件涨了 2.6GB，那不是网络下载）；
    #   - 读计数 >> 落盘：引擎在读本地文件做校验（S1 里出现过 260MB 只读不写）。
    seg_secs = max(1.0, n)
    seg_read = (cum[-1] - cum[0]) / seg_secs
    seg_disk = None
    if disk and disk[0] is not None and disk[-1] is not None:
        seg_disk = (disk[-1] - disk[0]) / seg_secs
    seg_nic = None
    if nic and len(nic) > 1:
        seg_nic = (nic[-1] - nic[0]) / seg_secs

    def _diverge(a, b):
        """两指标是否相差 3 倍以上（都为正才算）。"""
        return a > 0 and b > 0 and (a / b > 3 or b / a > 3)

    # 三指标互证。有网卡这个系统级真值时**以它为准**：只要有一个指标与它吻合，
    # 本段就成立，与它差 >3 倍的那个只是"对这个下载不可信"，记一笔即可。
    # （实测：ISO 下载时引擎读计数几乎完全不计数——整段 0.01MB 而落盘 1258MB，
    #  单看"读计数 vs 落盘"会误判整段作废，而网卡与落盘其实吻合得很好。）
    if seg_nic and seg_nic > 0:
        agree = [nm for nm, v in (("落盘", seg_disk), ("读计数", seg_read))
                 if v is not None and not _diverge(v, seg_nic)]
        if not agree:
            problems.append(
                f"网卡({fmt(seg_nic)}) 与落盘({fmt(seg_disk or 0)})、"
                f"读计数({fmt(seg_read)}) 都不吻合（>3 倍），三指标互不通气")
        elif len(agree) == 1:
            bad = "读计数" if "读计数" not in agree else "落盘"
            print(f"  [i] {bad} 与网卡差 >3 倍 → 本段以网卡为准"
                  f"（{bad}对本次下载不可信）")
    elif seg_disk is not None:
        # 没有网卡真值时才退回"读计数 vs 落盘"互证
        if seg_read > 0 and seg_disk > 0:
            if _diverge(seg_read, seg_disk):
                problems.append(
                    f"读计数({fmt(seg_read)}) 与落盘({fmt(seg_disk)}) 相差"
                    f"{seg_disk / seg_read:.1f} 倍，两指标都不敢采信")
        elif seg_read <= 0 < seg_disk:
            problems.append(f"读计数为 0 但落盘在涨（{fmt(seg_disk)}）：疑似预分配/秒传")

    # 优先级：网卡（系统级）> 落盘（目标文件）> 读计数（含本地读）
    if n_pct is not None:
        pct, src, early_ref = n_pct, "网卡收包", n_early
    elif d_pct is not None:
        pct, src, early_ref = d_pct, "落盘字节", d_early
    else:
        pct, src, early_ref = decay_pct, "读计数", early_bps
    if problems:
        print(f"  判定：本段作废 -> {'; '.join(problems)}")
    elif early_ref < 500 * 1024:
        print(f"  判定：本段速率过低（首窗 {fmt(early_ref)}），无法判定衰减")
    elif pct < 45:
        print(f"  判定：**衰减**（{src}：末段只有首段的 {pct:.0f}%）")
    elif pct < 70:
        print(f"  判定：部分衰减（{src}：末段为首段的 {pct:.0f}%）")
    else:
        print(f"  判定：**稳住**（{src}：末段/首段 = {pct:.0f}%）")
    print(f"  CSV: {path}")
    return 0


# ------------------------------------------------------------------ verify
def cmd_verify(args):
    """在真实客户端上验证 hook 生效（不需要有下载在进行）。

    步骤：注入 → 设倍率 → 自测节拍临时拉到 2s → 连续读 4 次自检 → 判定。
    这是 M1-b 的第一步：先证明"hook 在客户端里真的生效"，再谈衰减。
    """
    procs = baidu_procs()
    if not procs:
        print("!! 客户端未运行")
        return 1
    ep = engine_pid(procs)
    targets = [int(args.pid)] if args.pid else ([ep] if ep else [])
    if not targets:
        print("!! 找不到下载引擎进程")
        return 1
    print(f"[目标] 下载引擎 pid={targets[0]} "
          f"WOW64={is_wow64(targets[0])}（True → hook32.dll）")

    for pid in targets:
        print(f"\n[inject {pid}] {booster('inject', pid)}")
    time.sleep(1.5)
    print(f"[setspeed {args.factor} all] {booster('setspeed', args.factor, 'all')}")
    print(f"[probe 2000 all] {booster('probe', 2000, 'all')}")
    time.sleep(0.5)

    ok_all = True
    for pid in targets:
        print(f"\n--- pid={pid} ---")
        for i in range(5):
            time.sleep(1.0)
            st = parse_status(booster("status", pid))
            ok, bad = check_invariants(st, args.factor, True)
            print(f"  t={i+1}s status={st.get('status')} sentinel={st.get('sentinel')} "
                  f"stage2={st.get('stage2State')} winmmOk={st.get('winmmOk')} "
                  f"refresh={st.get('refreshCount')} qpcF={st.get('qpcFactor')} "
                  f"actualA1={st.get('actualA1')} probe={st.get('probeRatio')} "
                  f"probeMs={st.get('probeRealMs')} hb={st.get('heartbeat')} "
                  f"→ {'OK' if ok else 'BAD: ' + '; '.join(bad)}")
            if i >= 3 and not ok:
                ok_all = False
    print(f"\n===== 结论：{'客户端内 hook 生效 ✔' if ok_all else '存在不通过项 ✘'} =====")
    return 0 if ok_all else 1


# ------------------------------------------------------------------ report
def cmd_report(args):
    rows = []
    for path in args.csv:
        with open(path, "r", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if r.get("label"):
                    rows.append(r)
    if not rows:
        print("CSV 里没有数据")
        return 1
    labels = []
    for r in rows:
        if r["label"] not in labels:
            labels.append(r["label"])
    has_cum = "cum_engine_mb" in rows[0]
    has_disk = "disk_mb" in rows[0]
    print(f"===== 对照实验汇总（{' + '.join(args.csv)}）=====")
    if not has_cum:
        print("!! 旧 schema：只能给瞬时均值，衰减判据请用 run 的输出")
    print(f"{'段':<14}{'样本':>6}{'均值':>11}{'读末/首':>9}"
          f"{'落盘末/首':>10}{'网卡末/首':>10}{'停摆':>7}  判定")
    for lb in labels:
        seg = [r for r in rows if r["label"] == lb]
        n = len(seg)
        try:
            bps = [float(r["total_bps"]) for r in seg]
        except ValueError:
            continue
        win = max(1, min(args.window, n))
        has_disk_seg = "disk_mb" in seg[0]

        def wb(series, a, b):
            """CSV 里的累计列只有 n 项（无 t0 基线），索引与 cmd_run 差一位：
            第 i 项 = 从段开始到第 i 个样本的增量，所以窗口 [a,b) 的增量为
            series[b-1] - (series[a-1] if a>0 else 0)。"""
            if b <= a or b > len(series):
                return 0.0
            lo = series[a - 1] if a > 0 else 0.0
            return max(0.0, series[b - 1] - lo)

        cum = []
        if has_cum:
            try:
                cum = [float(r.get("cum_engine_mb") or 0) * 1e6 for r in seg]
            except ValueError:
                cum = [0.0] * n

        # 截掉"文件下完之后"的空尾巴：客户端下完会把分片重命名，之后 NIC/落盘
        # 都不再涨，整段看着像"衰减到 0"，其实只是没东西可下了。
        # 判据：最后一个 NIC 或落盘仍有增长的样本。不做这步，所有跑满的段
        # 都会被误报成"衰减"（实测 F5/F8c/F10 全中招）。
        def _series(key):
            try:
                return [float(r.get(key) or 0) * 1e6 for r in seg]
            except ValueError:
                return None

        act_end = None
        for key in ("nic_mb", "disk_mb"):
            s = _series(key)
            if not s or not any(s):
                continue
            e = 0
            for i in range(1, n):
                if s[i] - s[i - 1] > 200 * 1024:
                    e = i
            if e > 0:
                act_end = e + 1 if act_end is None else min(act_end, e + 1)
        truncated = act_end is not None and act_end < n
        if truncated:
            n_eff = act_end
            seg = seg[:n_eff]
        else:
            n_eff = n
        if n_eff < 2 * win:
            win_eff = max(1, n_eff // 2)
        else:
            win_eff = win

        early = wb(cum, 0, win_eff) / win_eff
        late = wb(cum, n_eff - win_eff, n_eff) / win_eff
        ratio = late / early * 100 if early > 0 else 0.0

        # 落盘地面真值（本段自己的 schema 决定有没有）
        dratio = None
        if has_disk_seg:
            dsk = _series("disk_mb")
            if dsk and any(dsk):
                de = wb(dsk, 0, win_eff) / win_eff
                dl = wb(dsk, n_eff - win_eff, n_eff) / win_eff
                dratio = dl / de * 100 if de > 0 else 0.0

        # 网卡收包：三指标里最可信（系统级），有则优先
        nratio = None
        nsk = _series("nic_mb")
        if nsk and any(nsk):
            ne = wb(nsk, 0, win_eff) / win_eff
            nl = wb(nsk, n_eff - win_eff, n_eff) / win_eff
            nratio = nl / ne * 100 if ne > 0 else 0.0

        problems = []
        try:
            inv = [int(r.get("inv_ok") or 0) for r in seg]
            if inv and sum(inv) / len(inv) <= 0.9:
                problems.append(f"不变量{sum(inv)/len(inv)*100:.0f}%")
        except ValueError:
            pass
        if "hook_present" in seg[0]:
            hp = sorted(set(r.get("hook_present") or "?" for r in seg))
            want = sorted(set("1" if (r.get("factor") or "").strip() else "0"
                              for r in seg))
            if hp != want:
                problems.append(f"hook在位{hp}≠声明{want}")
        if "engine_pid" in seg[0]:
            eps = set(r.get("engine_pid") for r in seg)
            if len(eps) > 1:
                problems.append(f"引擎pid变了{len(eps)}次")
        stall = None
        if "stalled" in seg[0]:
            try:
                sf = [int(r.get("stalled") or 0) for r in seg]
                stall = sum(sf) / len(sf)
                if stall > args.stall_gate:
                    problems.append(f"停摆{stall*100:.0f}%")
            except ValueError:
                pass

        use = nratio if nratio is not None else (
            dratio if dratio is not None else ratio)
        if problems:
            verdict = "作废: " + "; ".join(problems)
        elif not has_cum:
            verdict = "（旧 schema）"
        elif nratio is None and dratio is None and early < 500 * 1024:
            verdict = "速率过低"
        elif use < 45:
            verdict = "衰减"
        elif use < 70:
            verdict = "部分衰减"
        else:
            verdict = "稳住"
        st_s = f"{stall*100:.0f}%" if stall is not None else "-"
        d_s = f"{dratio:.0f}%" if dratio is not None else "-"
        n_s = f"{nratio:.0f}%" if nratio is not None else "-"
        note = f"  （已按下完点截断到 {n_eff}s）" if truncated else ""
        print(f"{lb:<14}{n:>6}{fmt(statistics.mean(bps)):>11}{ratio:>8.0f}%"
              f"{d_s:>10}{n_s:>10}{st_s:>7}  {verdict}{note}")
    print(f"\n注意：判定优先用「网卡末/首」（系统级网络真值）→ 落盘 → 读计数。"
          f"窗口固定 {args.window}s。网卡绝对值约偏高 25%，只看比值。")
    return 0


def main():
    ap = argparse.ArgumentParser(description="真实客户端 Booster 对照实验")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check", help="客户端/下载就绪检查")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("run", help="采集一段速率曲线")
    p.add_argument("--label", required=True)
    p.add_argument("--seconds", type=int, default=120)
    p.add_argument("--factor", type=float, default=None)
    p.add_argument("--inject", action="store_true")
    p.add_argument("--eject", action="store_true", help="先卸载 hook（跑无 hook 对照段用）")
    p.add_argument("--probe", default=None, help="自测节拍 ms 或 off")
    p.add_argument("--pid", default=None, help="只对某个 pid 操作（默认自动探测引擎）")
    p.add_argument("--csv", default="")
    p.add_argument("--progress-file", default="",
                   help="目标 .downloading 分片路径：按它的真实落盘量做地面真值判据")
    p.add_argument("--window", type=int, default=60,
                   help="衰减判据的窗口秒数（固定值，段间可比）")
    p.add_argument("--expect-hook", choices=("auto", "on", "off"), default="auto",
                   help="本段声明应否有 hook（auto = 按是否传 --factor/--inject 推断）")
    p.add_argument("--stall-warn", type=float, default=0.15,
                   help="停摆占比警戒线（超过只提示）")
    p.add_argument("--stall-gate", type=float, default=0.30,
                   help="停摆占比作废线（超过则本段不作衰减证据）")
    p.add_argument("--no-nic", action="store_true",
                   help="不采网卡收包（默认采集，作为系统级网络地面真值）")
    p.add_argument("--hold-factor", action="store_true",
                   help="每 15s 重申一次倍率：防止页面/后端在测量中途改写倍率把本段污染")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("verify", help="在真实客户端上验证 hook 生效（不需下载）")
    p.add_argument("--factor", type=float, default=5.0)
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("report", help="汇总 CSV（可给多个文件，按 label 合并）")
    p.add_argument("csv", nargs="+", default=[CSV_DEFAULT])
    p.add_argument("--window", type=int, default=60)
    p.add_argument("--stall-gate", type=float, default=0.30)
    p.set_defaults(func=cmd_report)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
