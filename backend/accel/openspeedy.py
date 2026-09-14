"""OpenSpeedy 桥接模块：百度客户端进程发现、注入控制、倍率调节、IO 速率采样。

供 Booster Pilot（M0.5 过渡闭环）与后续 M3 集成复用。OpenSpeedy 本体不修改：
- 注入/开关/倍率经其官方 bridge CLI（bridge32/64.exe 单发命令）或命名管道；
- 测速用进程 IO ReadTransferCount 增量（ctypes，无第三方依赖），
  网络下载字节主要体现在 ReadTransferCount（客户端从网络读、往磁盘写）。

安全约定：本模块只与 OpenSpeedy 官方组件和本机进程枚举交互，不触碰任何凭证。
"""
import csv
import ctypes
import io
import os
import subprocess
import sys
import time

# 本模块只在 Windows 上有意义，但 accel 蓝图还托管跨平台功能，
# 所以导入必须安全：非 Windows 上没有 ctypes.windll。
IS_WINDOWS = sys.platform.startswith("win")

# ---------------------------------------------------------------- 常量
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# 百度网盘客户端相关进程（doc2 已实测的进程清单）
BAIDU_PROCESS_NAMES = {
    "BaiduNetdisk.exe",
    "BaiduNetdiskUnite.exe",
    "baidunetdiskhost.exe",
    "YunDetectService.exe",
}
# 主下载进程优先（M0 Probe 2 会确认哪个真正承载下载引擎 YunDls.dll）
PRIMARY_PROCESS_NAMES = {"BaiduNetdisk.exe"}

# 真正承载下载引擎的进程（2026-09-13 实测）：
# 下载流量与 kernel.dll 的 qingluan 下载模块都在 baidunetdiskhost.exe 里，
# BaiduNetdisk.exe 只是壳（读速率长期 0）。注入必须打在这个进程上，
# 否则时间加速对下载完全无效——这正是首轮"倍率无效"的根因。
ENGINE_PROCESS_NAMES = {"baidunetdiskhost.exe"}

# 下载引擎的标志模块（2026-09-13 实测）：真正跑下载的 host 加载了
# kernel.dll（qingluan 下载模块）+ netbase.dll；同名的另一个 host 只加载
# vastplayer.dll（视频插件），完全没有下载能力。
# 所以"哪个 host 是引擎"必须按模块判定——只按进程名会把插件宿主也算进去，
# 于是"注入成功"却毫无作用，重演 V1/V2 那个"倍率无效"的假象。
ENGINE_MODULE = "kernel.dll"

# OpenSpeedy 常见安装位置（doc2 实测：D:\A2000-soft\百度网盘提速）
DEFAULT_INSTALL_DIRS = [
    r"D:\A2000-soft\百度网盘提速",
    r"C:\Program Files\OpenSpeedy",
    r"C:\Program Files (x86)\OpenSpeedy",
    r"D:\Program Files\OpenSpeedy",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\OpenSpeedy"),
    os.path.expandvars(r"%ProgramFiles%\OpenSpeedy"),
]

PIPES = (r"\\.\pipe\OpenSpeedyBridge64", r"\\.\pipe\OpenSpeedyBridge32")


class OpenSpeedyError(Exception):
    pass


# ---------------------------------------------------------------- 进程发现
def _tasklist() -> str:
    """tasklist 输出是本地 ANSI 编码（中文系统 GBK），显式按 mbcs 解码。"""
    r = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True,
                       timeout=15, encoding="mbcs", errors="replace")
    return r.stdout or ""


def list_baidu_processes() -> list:
    """枚举百度网盘相关进程，返回 [{name, pid}]（tasklist，无依赖）。"""
    procs = []
    for row in csv.reader(io.StringIO(_tasklist())):
        if len(row) >= 2 and row[0].strip() in BAIDU_PROCESS_NAMES:
            try:
                procs.append({"name": row[0].strip(), "pid": int(row[1])})
            except ValueError:
                continue
    return procs


def list_all_processes() -> list:
    procs = []
    for row in csv.reader(io.StringIO(_tasklist())):
        if len(row) >= 2:
            try:
                procs.append({"name": row[0].strip(), "pid": int(row[1])})
            except ValueError:
                continue
    return procs


# ---------------------------------------------------------------- bridge 定位
def find_openspeedy_dir(extra_dirs=None) -> str:
    """定位 OpenSpeedy 安装目录（含 bridge32/64.exe）。"""
    dirs = list(DEFAULT_INSTALL_DIRS) + list(extra_dirs or [])
    for d in dirs:
        if any(os.path.isfile(os.path.join(d, f))
               for f in ("bridge32.exe", "bridge64.exe")):
            return d
    return ""


def find_bridge_exe(extra_dirs=None) -> str:
    d = find_openspeedy_dir(extra_dirs)
    if not d:
        return ""
    # 客户端是 32 位（WOW64，doc2 实测）→ bridge32 优先
    for name in ("bridge32.exe", "bridge64.exe"):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return ""


# ---------------------------------------------------------------- bridge 命令
#
# 模块枚举必须用 toolhelp，不能用 `tasklist /M <dll>`：实测 tasklist /M 对
# WOW64 进程**看不到 32 位模块**，kernel.dll / hook32.dll / speedpatch32.dll
# 全部静默返回空。旧版 module_loaded() 就是 tasklist 实现，因此
# BoosterManager.inject() 里的"OpenSpeedy speedpatch 冲突"检查从未真正生效过。
TH32CS_SNAPMODULE = 0x8
TH32CS_SNAPMODULE32 = 0x10


class _MODULEENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", ctypes.c_ulong), ("th32ModuleID", ctypes.c_ulong),
                ("th32ProcessID", ctypes.c_ulong), ("GlblcntUsage", ctypes.c_ulong),
                ("ProccntUsage", ctypes.c_ulong), ("modBaseAddr", ctypes.c_void_p),
                ("modBaseSize", ctypes.c_ulong), ("hModule", ctypes.c_void_p),
                ("szModule", ctypes.c_wchar * 256),
                ("szExePath", ctypes.c_wchar * 260)]


def mods(pid: int) -> list:
    """枚举进程已加载模块名（toolhelp；SNAPMODULE|SNAPMODULE32 兼容 WOW64）。"""
    for flags in (TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, TH32CS_SNAPMODULE32):
        snap = _k32.CreateToolhelp32Snapshot(flags, int(pid))
        if not snap or snap == ctypes.c_void_p(-1).value:
            continue
        out, me = [], _MODULEENTRY32W()
        me.dwSize = ctypes.sizeof(me)
        ok = _k32.Module32FirstW(ctypes.c_void_p(snap), ctypes.byref(me))
        while ok:
            out.append(me.szModule)
            ok = _k32.Module32NextW(ctypes.c_void_p(snap), ctypes.byref(me))
        _k32.CloseHandle(ctypes.c_void_p(snap))
        if out:
            return out
    return []


def module_loaded(pid: int, module: str) -> bool:
    """进程是否加载了指定模块（大小写不敏感，toolhelp 实现）。"""
    want = module.lower()
    return any(m.lower() == want for m in mods(pid))


def pids_with_module(module: str, pids=None) -> set:
    """哪些 pid 加载了指定模块。给了 pids 就只查这些（省时间）。

    判定下载引擎用它（见 ENGINE_MODULE）：只有加载 kernel.dll 的 host 才
    真的跑下载。用 tasklist /M 会得到空集，千万不要回退到那个实现。
    """
    want = module.lower()
    if pids is None:
        pids = [p["pid"] for p in list_all_processes()]
    return {int(p) for p in pids if any(m.lower() == want for m in mods(p))}


def bridge_call(bridge_exe: str, command: str, timeout: int = 10) -> str:
    """单发命令：bridge32.exe "SETSPEED 5.0" → stdout（OK ... / ERROR ...）。"""
    if not bridge_exe or not os.path.isfile(bridge_exe):
        raise OpenSpeedyError("未找到 bridge 可执行文件（请确认 OpenSpeedy 已安装）")
    r = subprocess.run([bridge_exe, command], capture_output=True,
                       text=True, timeout=timeout)
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    return out or err or "(无输出)"


def inject(bridge_exe: str, pid: int) -> str:
    return bridge_call(bridge_exe, f"INJECT {pid}")


def enable(bridge_exe: str, pid: int) -> str:
    return bridge_call(bridge_exe, f"ENABLE {pid}")


def disable(bridge_exe: str, pid: int) -> str:
    return bridge_call(bridge_exe, f"DISABLE {pid}")


def set_speed(bridge_exe: str, factor: float) -> str:
    return bridge_call(bridge_exe, f"SETSPEED {factor}")


def get_speed(bridge_exe: str) -> str:
    return bridge_call(bridge_exe, "GETSPEED")


# ---------------------------------------------------------------- IO 速率传感器
class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


_k32 = ctypes.windll.kernel32 if IS_WINDOWS else None

# x64 句柄完整性（同 booster.py）
if _k32 is not None:
    _k32.OpenProcess.restype = ctypes.c_void_p


def read_transfer_bytes(pid: int):
    """进程累计网络读字节数（IO_COUNTERS.ReadTransferCount）；失败返回 None。"""
    h = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        io_counters = _IO_COUNTERS()
        if not _k32.GetProcessIoCounters(h, ctypes.byref(io_counters)):
            return None
        return int(io_counters.ReadTransferCount)
    finally:
        _k32.CloseHandle(h)


def total_read_bytes(pids: list) -> int:
    """多个进程的累计网络读字节之和（忽略已退出的进程）。"""
    total = 0
    for pid in pids:
        v = read_transfer_bytes(pid)
        if v:
            total += v
    return total


class SpeedSampler:
    """按 pids 集合采样网络读速率（字节/秒）。进程集合变化时自动重置基线。"""

    def __init__(self):
        self._last_total = None
        self._last_t = None

    def sample(self, pids: list) -> float:
        total = total_read_bytes(pids)
        now = time.time()
        speed = 0.0
        if self._last_total is not None and self._last_t is not None \
                and total >= self._last_total and now > self._last_t:
            speed = (total - self._last_total) / (now - self._last_t)
        self._last_total, self._last_t = total, now
        return max(0.0, speed)
