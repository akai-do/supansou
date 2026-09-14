"""Booster 管理器：自研选择性 Hook（M1 DLL）的 Python 闭环控制器。

架构（比原计划的 C++ 控制器 + HTTP API 大幅简化，功能等价）：
  hook32/64.dll（M1 产物，已构建）注入百度客户端进程，每秒轮询共享内存
  DuPanBooster.<pid> 中的倍率/分组/开关；本模块用 ctypes：
    - 注入：CreateRemoteThread + LoadLibraryW（写入 DLL 路径）
    - 控制面：CreateFileMappingW + MapViewOfFile 创建/打开 DuPanBooster.<pid>，
      seqlock 写侧更新 revision/enabled/mask/factors
    - 测速：进程 IO ReadTransferCount 增量（复用 openspeedy 采样器）
    - 闭环：真实吞吐反馈状态机（塌陷减半、稳定上探、归零恢复、
      服务端限速识别），1s 节拍线程
  站长端点经 /api/accel/booster/* 暴露；绝不加入访客白名单。

安全约定：本模块不触碰任何百度凭证；注入目标仅限百度客户端白名单进程。
"""
import ctypes
import os
import subprocess
import sys
import threading
import time
from collections import deque

from . import netmon
from . import openspeedy

# 客户端加速（时间 Hook 注入）只在 Windows 上有意义，但本模块被 accel 蓝图导入，
# 而 accel 还托管"访客直连解析"这类跨平台功能 —— 所以**导入必须安全**：
# 非 Windows 上 `ctypes.windll` 根本不存在，直接访问会让整个后端起不来
# （实测：部署到 Linux 服务器时崩在 import 阶段）。
IS_WINDOWS = sys.platform.startswith("win")

_k32 = ctypes.windll.kernel32 if IS_WINDOWS else None

# x64 下句柄/指针是 64 位：不设 restype 会被截断成 32 位（GetModuleHandleW
# 返回的模块基址超 4GB，截断后 GetProcAddress 必然失败）
if _k32 is not None:
    _k32.OpenProcess.restype = ctypes.c_void_p
    _k32.GetModuleHandleW.restype = ctypes.c_void_p
    _k32.GetProcAddress.restype = ctypes.c_void_p
    _k32.GetProcAddress.argtypes = (ctypes.c_void_p, ctypes.c_char_p)
    _k32.VirtualAllocEx.restype = ctypes.c_void_p
    _k32.MapViewOfFile.restype = ctypes.c_void_p

# ---- Win32 常量 ----
PROCESS_CREATE_THREAD = 0x0002
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_READ = 0x0010
MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000
MEM_RELEASE = 0x8000
PAGE_READWRITE = 0x04
FILE_MAP_ALL_ACCESS = 0xF001F
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1)
ERROR_ALREADY_EXISTS = 183

GROUP_A1, GROUP_A2, GROUP_B = 1, 2, 4
DEFAULT_MASK = GROUP_A1 | GROUP_A2 | GROUP_B
# 速率测量不变量：B 组（Sleep/WaitFor*）常驻 mask 且因子恒 1.0。
# 摘掉 B 会让 RefreshIfChanged 的门控把 A2 也按 1.0 走（等于什么都不缩放）；
# 缩放 B 会让 SelfProbe 的"真实 1s"窗口失真（probeRatio 变成 F²）。
RATE_SAFE_MASK = GROUP_A1 | GROUP_A2
RATE_SAFE_FACTOR_B = 1.0

# 闭环参数（V4 用真实客户端曲线重标，证据见各项注释）
#
# 速度源已从 openspeedy.SpeedSampler（GetProcessIoCounters.ReadTransferCount）
# 换成 netmon.NicSpeedSampler（网卡收包）。原因：读计数把网络读与本地文件读混算，
# 实测与落盘量/网卡量差 9.6~40 倍，用它调参会"实际在高速下载、闭环却看到
# 13KB/s 然后一直降档"（15:48 那次自动调速就是这么跑偏的）。
LADDER = [1, 2, 3, 4, 5, 6, 8, 10, 15]
# 10s 窗口对本客户端太短：瞬时速率本身是"分片方波"（4.19MB/s ↔ ~10KB/s），
# 10s 常整段落在一个空档里 → 误判塌陷 → 一直降档。实测真实衰减是在 ~300s
# 尺度上展开的（F=5：首 60s 8.84MB/s → 末 60s 4.63MB/s），所以用 60s。
WIN_SEC = 60
# 实测 F=5 的末/首比值：落盘口径 52%、读计数口径 15%。0.45 在读计数口径下会
# 持续误触发；0.55 既能抓到期真实衰减，又不至于被方波噪声打穿。
COLLAPSE_RATIO = 0.55
COLLAPSE_SNAPS = 2
BASE_WINDOWS = 3
# 网卡口径下机器闲置噪声约 10~150KB/s，100KB/s 太低会被噪声当成"归零"。
FLOOR_BPS = 300 * 1024
RECOVER_LIMIT = 3
SERVER_COOLDOWN = 600


class BoosterShared(ctypes.Structure):
    """与 native/hook/booster_shared.h 严格对应的共享状态块。

    注意：字段顺序/类型必须与 C 头逐字一致，否则 seqlock 读到的全是垃圾。
    改这里必须同时改 native/hook/booster_shared.h 并重新编译三个产物。
    """
    _fields_ = [
        ("seq", ctypes.c_ulong),
        ("revision", ctypes.c_ulong),
        ("enabled", ctypes.c_ulong),
        ("groupMask", ctypes.c_ulong),
        ("factorA1", ctypes.c_double),
        ("factorA2", ctypes.c_double),
        ("factorB", ctypes.c_double),
        ("uninstall", ctypes.c_ulonglong),
        ("requestedA1", ctypes.c_double),
        ("actualA1", ctypes.c_double),
        ("sentinel", ctypes.c_ulonglong),
        ("status", ctypes.c_ulonglong),
        ("detail", ctypes.c_ulonglong),
        ("heartbeat", ctypes.c_ulonglong),
        ("monitorTicks", ctypes.c_ulonglong),
        ("winmmOk", ctypes.c_ulonglong),
        ("stage2State", ctypes.c_ulonglong),
        ("probeRatio", ctypes.c_double),
        ("probeRealMs", ctypes.c_double),
        ("qpcFactor", ctypes.c_double),
        ("tickFactor", ctypes.c_double),
        ("refreshCount", ctypes.c_ulonglong),
        ("probePeriodMs", ctypes.c_ulonglong),
    ]


# 自测节拍（与 booster_shared.h 的 PROBE_* 一致）
# 接真实客户端用 10s：自测的 1s 真实等待被摊薄到 ~10%，不再干扰速率测量；
# 测速对照实验用 PROBE_OFF 完全关掉（probeRatio 会保持上一次的值）。
PROBE_PERIOD_DEFAULT = 10000
PROBE_PERIOD_MIN = 1000
PROBE_OFF = 0xFFFFFFFF


# DLL 上报状态码（与 booster_shared.h 的 HOOK_STATUS_* 一致）
STATUS_TEXT = {
    0: "未上报", 7: "已附着（hook 装填中）", 1: "生效中",
    2: "MinHook 初始化失败", 3: "安装 hook 失败",
    4: "系统 API 解析失败", 5: "启用 hook 失败", 6: "运行中",
    8: "winmm hook 安装失败", 9: "安装过程异常", 10: "共享内存不可用",
}
SENTINEL = 0x424F4F53


class OpenSpeedyHookConflict(Exception):
    pass


class SharedState:
    """单进程的共享内存控制面。"""

    def __init__(self, pid: int):
        self.pid = pid
        name = f"DuPanBooster.{pid}"
        self.map_ = _k32.CreateFileMappingW(
            INVALID_HANDLE_VALUE, None, PAGE_READWRITE, 0, 256, name)
        if not self.map_:
            raise OSError(f"CreateFileMappingW 失败 err={_k32.GetLastError()}")
        existed = _k32.GetLastError() == ERROR_ALREADY_EXISTS
        view_addr = _k32.MapViewOfFile(self.map_, FILE_MAP_ALL_ACCESS, 0, 0, 256)
        if not view_addr:
            raise OSError(f"MapViewOfFile 失败 err={_k32.GetLastError()}")
        # 共享内存上的结构体视图（零拷贝，写入直达目标进程可读的内存）
        self._view_addr = view_addr
        self._sh = BoosterShared.from_address(view_addr)
        self.revision = 0
        if existed:
            self.revision = self._sh.revision

    def write(self, enabled: int, mask: int, fA1: float, fA2: float,
              fB: float, uninstall: int = 0, probe_period_ms: int = None):
        sh = self._sh
        sh.seq = sh.seq + 1               # odd：写入中
        sh.revision = self.revision + 1
        sh.enabled = enabled
        sh.groupMask = mask
        sh.factorA1 = fA1
        sh.factorA2 = fA2
        sh.factorB = fB
        sh.uninstall = uninstall
        # 自测节拍：None = 不改（保持 DLL/控制器已设的值，避免每次写都清零）
        if probe_period_ms is not None:
            sh.probePeriodMs = probe_period_ms
        sh.seq = sh.seq + 1               # even：稳定
        self.revision = sh.revision

    def read(self) -> BoosterShared:
        # seqlock 读：seq 偶数且前后一致才算完整快照
        for _ in range(50):
            s1 = self._sh.seq
            if s1 & 1:
                time.sleep(0.001)
                continue
            snap = BoosterShared(
                enabled=self._sh.enabled,
                groupMask=self._sh.groupMask,
                factorA1=self._sh.factorA1,
                factorA2=self._sh.factorA2,
                factorB=self._sh.factorB,
                uninstall=self._sh.uninstall,
                actualA1=self._sh.actualA1,
                sentinel=self._sh.sentinel,
                status=self._sh.status,
                detail=self._sh.detail,
                heartbeat=self._sh.heartbeat,
                winmmOk=self._sh.winmmOk,
                stage2State=self._sh.stage2State,
                probeRatio=self._sh.probeRatio,
                probeRealMs=self._sh.probeRealMs,
                qpcFactor=self._sh.qpcFactor,
                tickFactor=self._sh.tickFactor,
                refreshCount=self._sh.refreshCount,
                probePeriodMs=self._sh.probePeriodMs,
            )
            s2 = self._sh.seq
            if s1 == s2:
                return snap
            time.sleep(0.001)
        return BoosterShared()

    def dll_health(self):
        """读取 DLL 自检结论：(ok, 描述)。区分「没加载」与「加载了但 hook 没装上」。"""
        sh = self.read()
        if sh.sentinel != SENTINEL:
            return False, "hook DLL 未附着（注入路径或 DllMain 失败）"
        st = STATUS_TEXT.get(sh.status, f"未知状态 {sh.status}")
        if sh.status != 1:
            return False, f"DLL 已附着但未生效：{st}（detail={sh.detail}）"
        if sh.probeRealMs > 100 and sh.probeRatio > 0:
            return True, f"生效中，自测倍率 {sh.probeRatio:.2f}x（{st}）"
        return True, f"已安装（{st}），自测未完成"

    def close(self):
        # 注意：视图地址不在字段里（from_address 得到的是别名），单独留一份
        if getattr(self, "_view_addr", None):
            _k32.UnmapViewOfFile(ctypes.c_void_p(self._view_addr))
            self._view_addr = None
        if self.map_:
            _k32.CloseHandle(self.map_)
            self.map_ = None


# ---- 注入（位数匹配助手进程） ----
# 不能在本进程里 CreateRemoteThread：本服务是 64 位，而百度客户端是 WOW64
# （32 位）。64 位 kernel32 里的 LoadLibraryW 地址在 32 位目标地址空间里毫无
# 意义，远程线程会直接返回 0，DLL 永远加载不上。所以按目标位数调用
# tools/booster/inject32.exe / inject64.exe。
_HELPER_EXIT = {
    0: "成功",
    1: "OpenProcess 被拒绝（需管理员权限）",
    2: "VirtualAllocEx 失败",
    3: "WriteProcessMemory 失败",
    4: "GetProcAddress 失败",
    5: "CreateRemoteThread 失败（可能被杀软拦截）",
    6: "LoadLibrary 返回 NULL（DLL 被目标进程拒绝加载）",
}


def inject_dll(pid: int, dll_path: str, dll_dir: str) -> str:
    """按目标位数调用注入助手；成功返回 'ok'，失败抛异常。"""
    wow64 = ctypes.c_int(0)
    h = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    is32 = False
    if h:
        _k32.IsWow64Process(ctypes.c_void_p(h), ctypes.byref(wow64))
        _k32.CloseHandle(ctypes.c_void_p(h))
        is32 = wow64.value != 0
    helper = os.path.join(dll_dir, "inject32.exe" if is32 else "inject64.exe")
    if not os.path.isfile(helper):
        raise OSError(f"缺少注入助手 {helper}（请先运行 native\\build.bat）")
    r = subprocess.run([helper, str(pid), dll_path], capture_output=True,
                       timeout=30, encoding="mbcs", errors="replace")
    if r.returncode == 0:
        return "ok"
    reason = _HELPER_EXIT.get(r.returncode, f"未知退出码 {r.returncode}")
    if r.returncode == 1:
        raise PermissionError(reason)
    raise OSError(reason)


def module_loaded(pid: int, module: str) -> bool:
    """tasklist /M 查询进程是否加载了指定模块。"""
    r = subprocess.run(["tasklist", "/M", module, "/FO", "CSV", "/NH"],
                       capture_output=True, timeout=15,
                       encoding="mbcs", errors="replace")
    for line in (r.stdout or "").splitlines():
        parts = line.split(",")
        if len(parts) >= 2 and parts[1].strip('"') == str(pid):
            return True
    return False


class BoosterManager:
    """站点侧控制器：注入/控制/闭环调速/状态聚合。"""

    def __init__(self, dll_dir: str):
        self.dll_dir = dll_dir
        self._lock = threading.Lock()
        self._states = {}               # pid → SharedState
        self._sampler = openspeedy.SpeedSampler()      # 旧口径（读计数，兜底）
        self._nic = netmon.NicSpeedSampler()           # 新口径（网卡，优先）
        self._speed_src = "nic"
        self._log = deque(maxlen=40)
        # 闭环状态
        self._auto = False
        self._target_mbps = 8.0
        # 默认倍率 = 实测「最高不衰减倍率」8（300s 满载窗口两次复现稳定：
        # 96% / 108%，约 16.4MB/s ≈ 基线的 6.3 倍）。F=10 在 300s 内单调
        # 下滑 19% 属衰减档，不作为默认。F=5 仍是保守档（4.2×，长窗最稳）。
        self._ladder_idx = LADDER.index(8)
        self._manual_factor = 8.0
        self._manual_override = False   # 用户拖滑杆后暂停自动
        self._state = "IDLE"
        self._recent = deque(maxlen=8)
        self._base_hist = deque(maxlen=BASE_WINDOWS)
        self._collapse_snaps = 0
        self._low_since = 0.0
        self._recover_count = 0
        self._server_cooldown_until = 0.0
        self._last_change = 0.0
        self._last_snapshot = 0.0
        self._ewma = 0.0
        self._tuner_thread = None
        self._stop = threading.Event()

    # ---------- 日志 ----------
    def note(self, msg: str):
        line = time.strftime("%H:%M:%S") + " " + msg
        self._log.append(line)
        return line

    # ---------- 目标进程 ----------
    def _engine_map(self, procs=None) -> dict:
        """{pid: {engine, module_ok, read_mb}} —— 按模块判定谁是真下载引擎。

        判定依据：baidunetdiskhost.exe 且加载了 kernel.dll（ENGINE_MODULE）。
        实测（2026-09-13）：两个同名 host 里只有一个加载 kernel.dll+netbase.dll，
        另一个只有 vastplayer.dll；只按进程名判定会把插件宿主也算成引擎，
        于是"注入成功"却毫无作用。
        """
        procs = procs if procs is not None else openspeedy.list_baidu_processes()
        hosts = [p for p in procs
                 if p["name"].lower() in openspeedy.ENGINE_PROCESS_NAMES]
        if not hosts:
            return {}
        have = openspeedy.pids_with_module(openspeedy.ENGINE_MODULE,
                                          [p["pid"] for p in hosts])
        m = {}
        for p in hosts:
            m[p["pid"]] = {
                "engine": p["pid"] in have,
                "module_ok": p["pid"] in have,
                "read_mb": (openspeedy.read_transfer_bytes(p["pid"]) or 0) / 1e6,
                "fallback": False,
            }
        if not any(v["engine"] for v in m.values()):
            # kernel.dll 尚未加载（客户端刚起、或当前没有下载任务）：
            # 退回"累计读最大者"，否则会完全找不到可注入目标。
            pid, v = max(m.items(), key=lambda kv: kv[1]["read_mb"])
            v["engine"] = True
            v["fallback"] = True
        return m

    def engine_pids(self) -> list:
        """真下载引擎的 pid 列表（推荐注入目标；engine_targets 的兼容包装）。"""
        return [pid for pid, v in self._engine_map().items() if v["engine"]]

    def engine_targets(self) -> list:
        """真下载引擎的详情（含判定证据，给前端/日志用）。"""
        procs = openspeedy.list_baidu_processes()
        em = self._engine_map(procs)
        name = {p["pid"]: p["name"] for p in procs}
        return [{"pid": pid, "name": name.get(pid, "?"), **v}
                for pid, v in em.items() if v["engine"]]

    def targets(self) -> list:
        procs = openspeedy.list_baidu_processes()
        em = self._engine_map(procs)
        out = []
        for p in procs:
            e = em.get(p["pid"], {})
            out.append({
                "pid": p["pid"], "name": p["name"],
                "engine": bool(e.get("engine")),
                # module_ok=False 表示这是"猜的"引擎（kernel.dll 还没加载）
                "module_ok": bool(e.get("module_ok")),
                "read_mb": e.get("read_mb"),
                "injected": p["pid"] in self._states,
            })
        return out

    def _pids(self) -> list:
        return [p["pid"] for p in openspeedy.list_baidu_processes()]

    # ---------- 可用性 ----------
    def dlls_ready(self):
        """(是否可用, 原因)。让 /booster/status 的 available 有真实含义。

        以前 available 恒为 True（booster 对象总能构造出来），而前端在首次请求
        返回前会拿初始值渲染 → 页面会"闪一下"报"组件不可用、请先构建 DLL"。
        现在后端真的检查产物，前端也不再拿"未知"当"不可用"。
        """
        if not IS_WINDOWS:
            return False, ("客户端加速只能在 Windows 上对**本机的**百度网盘客户端"
                           "注入生效；服务器/容器里不适用（这是设计如此）")
        missing = [n for n in ("hook32.dll", "hook64.dll")
                   if not os.path.isfile(os.path.join(self.dll_dir, n))]
        if missing:
            return False, ("缺少 " + "、".join(missing)
                           + r"，请运行 native\build.bat 后重启后端")
        return True, ""

    # ---------- 注入 / 卸载 ----------
    def _dll_for(self, pid: int) -> str:
        """按目标进程位数选 hook DLL（客户端是 WOW64 → hook32.dll）。"""
        try:
            h = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if h:
                wow64 = ctypes.c_int(0)
                _k32.IsWow64Process(h, ctypes.byref(wow64))
                _k32.CloseHandle(h)
                name = "hook32.dll" if wow64.value else "hook64.dll"
                p = os.path.join(self.dll_dir, name)
                if os.path.isfile(p):
                    return p
        except Exception:
            pass
        for name in ("hook64.dll", "hook32.dll"):
            p = os.path.join(self.dll_dir, name)
            if os.path.isfile(p):
                return p
        return ""

    def inject(self, pid: int) -> str:
        with self._lock:
            if pid in self._states:
                st = self._states[pid]
                st.write(1, DEFAULT_MASK, self._manual_factor,
                         self._manual_factor, 1.0)
                return f"pid {pid} 已注入，重新启用"
            dll = self._dll_for(pid)
            if not os.path.isfile(dll):
                return f"找不到 hook DLL：{dll}"
            if openspeedy.module_loaded(pid, "speedpatch32.dll") or \
                    openspeedy.module_loaded(pid, "speedpatch64.dll"):
                raise OpenSpeedyHookConflict(
                    "该进程已加载 OpenSpeedy 的 speedpatch（两套 hook 不能共存），"
                    "请先在 OpenSpeedy 中 eject 或重启百度网盘客户端")
            try:
                inject_dll(pid, dll, self.dll_dir)
            except (OSError, PermissionError) as e:
                return f"注入失败：{e}"
            # 注入成功后自检：DLL 是否真的附着、hook 是否真的装上。
            # 必须轮询：DLL 是异步装填的，刚附着时 status=7（"hook 装填中"）。
            # 立刻判定会把**每一次成功注入**都报成失败（实测踩到过）。
            try:
                st = SharedState(pid)
                st.write(1, DEFAULT_MASK, self._manual_factor,
                         self._manual_factor, 1.0)
                self._states[pid] = st
                # 终态失败：DLL 已附着但 hook 装不上，重试也没用
                terminal_bad = {2, 3, 4, 5, 8, 9, 10}
                ok, why = False, "未开始自检"
                deadline = time.time() + 3.0
                while True:
                    sh = st.read()
                    if sh.sentinel == SENTINEL and sh.status in terminal_bad:
                        why = (f"DLL 已附着但未生效："
                               f"{STATUS_TEXT.get(sh.status)}（detail={sh.detail}）")
                        break
                    ok, why = st.dll_health()
                    if ok or time.time() >= deadline:
                        break
                    time.sleep(0.15)
                if not ok:
                    self.note(f"注入 pid={pid} 自检未通过：{why}")
                    return f"注入 pid={pid} 但自检未通过：{why}"
                msg = self.note(f"注入成功 pid={pid}（{os.path.basename(dll)}，"
                                f"倍率 {self._manual_factor}）")
                return msg
            except OSError as e:
                return f"共享内存创建失败：{e}"

    def inject_all(self) -> dict:
        ok, fail = [], []
        for t in self.targets():
            try:
                msg = self.inject(t["pid"])
                ok.append(t["pid"])
            except OpenSpeedyHookConflict as e:
                fail.append(f"{t['pid']}: {e}")
            except Exception as e:
                fail.append(f"{t['pid']}: {e}")
        return {"ok": ok, "fail": fail}

    def inject_engine(self) -> dict:
        """只注入真正承载下载引擎的进程（推荐入口，按 kernel.dll 判定）。

        找不到带 kernel.dll 的 host 时**不猜**：直接报告"先开始一个下载"。
        因为随便挑一个同名 host 注入，只会得到"注入成功但倍率无效"的假象。
        """
        ok, fail = [], []
        tgs = self.engine_targets()
        if not tgs:
            return {"ok": ok, "targets": [],
                    "fail": ["找不到下载引擎进程（客户端未运行？）"]}
        for t in tgs:
            if t.get("fallback"):
                fail.append(f"{t['pid']}: kernel.dll 未加载，无法确认它是引擎；"
                            f"请先在客户端里开始一个下载再注入")
                continue
            try:
                msg = self.inject(t["pid"])
                if "失败" in msg or "未通过" in msg:
                    fail.append(f"{t['pid']}: {msg}")
                else:
                    ok.append(t["pid"])
            except Exception as e:
                fail.append(f"{t['pid']}: {e}")
        self.note(f"注入下载引擎进程：成功 {len(ok)}，失败 {len(fail)}")
        return {"ok": ok, "fail": fail, "targets": tgs}

    def eject_all(self) -> int:
        n = 0
        with self._lock:
            for pid, st in list(self._states.items()):
                try:
                    sh = st.read()
                    st.write(sh.enabled, sh.groupMask, sh.factorA1,
                             sh.factorA2, sh.factorB, uninstall=1)
                    n += 1
                except OSError:
                    pass
                st.close()
                self._states.pop(pid, None)
        self.note(f"已发送自卸载协议（{n} 个进程）")
        return n

    def set_enabled(self, on: bool):
        with self._lock:
            for pid, st in self._states.items():
                sh = st.read()
                st.write(1 if on else 0, sh.groupMask, sh.factorA1,
                         sh.factorA2, sh.factorB)

    def set_factor(self, f: float):
        f = max(1.0, min(16.0, f))
        self._manual_factor = f
        with self._lock:
            for pid, st in self._states.items():
                sh = st.read()
                # B 组常驻 mask 且因子恒 1.0（速率测量不变量，见文件头注释）
                st.write(sh.enabled, DEFAULT_MASK, f, f, RATE_SAFE_FACTOR_B)
        self.note(f"手动倍率 → {f}x（B 组透传）")

    def set_probe_period(self, period_ms: int):
        """调自测节拍：默认 10s；PROBE_OFF 关闭（测速对照实验用，零额外负载）。"""
        with self._lock:
            for pid, st in self._states.items():
                sh = st.read()
                st.write(sh.enabled, sh.groupMask, sh.factorA1, sh.factorA2,
                         sh.factorB, probe_period_ms=period_ms)
        self.note(f"自测节拍 → {period_ms}ms"
                  f"{'（已关闭）' if period_ms == PROBE_OFF else ''}")

    # ---------- 闭环调速线程 ----------
    def start_auto(self, target_mbps: float):
        self._target_mbps = max(1.0, target_mbps)
        self._auto = True
        self._manual_override = False
        if self._tuner_thread and self._tuner_thread.is_alive():
            return
        self._stop.clear()
        self._tuner_thread = threading.Thread(
            target=self._tuner_loop, daemon=True, name="booster-tuner")
        self._tuner_thread.start()
        self.note(f"闭环自动调速已启动（目标 {self._target_mbps}MB/s）")

    def stop_auto(self):
        self._auto = False
        self.note("闭环自动调速已停止")

    def _tuner_loop(self):
        # 速度源优先用网卡（系统级、不受预分配/本地校验读影响）；
        # 取不到网卡信息时回退到进程读计数（旧口径，只作兜底）。
        nic = netmon.NicSpeedSampler()
        fallback = openspeedy.SpeedSampler()
        self._speed_src = "nic"
        while not self._stop.is_set():
            time.sleep(1.0)
            if not self._auto:
                continue
            pids = [p["pid"] for p in openspeedy.list_baidu_processes()]
            if not pids:
                self._state = "IDLE（客户端未运行）"
                continue
            speed = nic.sample()
            if speed <= 0:
                fb = fallback.sample(pids)
                if fb > 0:
                    speed = fb
                    self._speed_src = "read(兜底)"
            now = time.time()
            self._ewma = self._ewma * 0.6 + speed * 0.4
            self._recent.append(speed)

            factor = LADDER[self._ladder_idx]
            snap_due = now - self._last_snapshot >= WIN_SEC
            if snap_due and len(self._recent) >= 5:
                self._last_snapshot = now
                recent_avg = sum(self._recent) / len(self._recent)
                base_avg = (sum(self._base_hist) / len(self._base_hist)
                            if self._base_hist else 0.0)

                # 归零恢复
                if base_avg > FLOOR_BPS and recent_avg < FLOOR_BPS:
                    if not self._low_since:
                        self._low_since = now
                    elif now - self._low_since > 10:
                        self.note("速率归零超 10s → 重新 ENABLE 全部进程")
                        self.set_enabled(True)
                        self._ladder_idx = 0
                        self._apply_factor(LADDER[0])
                        self._base_hist.clear()
                        self._recent.clear()
                        self._low_since = now
                        self._state = "RECOVER"
                        continue
                else:
                    self._low_since = 0.0

                # 服务端限速识别
                if self._recover_count > RECOVER_LIMIT:
                    self._server_cooldown_until = now + SERVER_COOLDOWN
                    self._recover_count = 0
                    self.note("连续恢复无效 → 瓶颈在服务端账号限速"
                              f"（冷却 {SERVER_COOLDOWN // 60} 分钟后自动重探）")
                    self._state = "SERVER_LIMITED"
                elif now < self._server_cooldown_until:
                    self._state = "SERVER_LIMITED（冷却中）"
                    continue

                # 塌陷（窗口对比 + 持续性）
                if base_avg > 500 * 1024 and len(self._base_hist) >= 2 \
                        and recent_avg < base_avg * COLLAPSE_RATIO:
                    self._collapse_snaps += 1
                else:
                    self._collapse_snaps = 0
                if self._collapse_snaps >= COLLAPSE_SNAPS \
                        and now - self._last_change > 8:
                    self._collapse_snaps = 0
                    if self._ladder_idx > 0:
                        new_idx = max(0, self._ladder_idx - 2)
                        self.note(f"塌陷（{recent_avg/1e3:.0f}KB/s < 45%×基线"
                                  f" {base_avg/1e3:.0f}KB/s）→ "
                                  f"倍率 {LADDER[self._ladder_idx]}→{LADDER[new_idx]}")
                        self._ladder_idx = new_idx
                        self._apply_factor(LADDER[new_idx])
                        self._base_hist.clear()
                        self._recent.clear()
                        self._last_change = now
                        self._state = "BACKOFF"
                    else:
                        self._recover_count += 1
                        self.note(f"底档塌陷 → RECOVER#{self._recover_count}："
                                  f"重新 ENABLE 全部进程（倍率回 {LADDER[2]}x）")
                        self.set_enabled(True)
                        self._ladder_idx = 2
                        self._apply_factor(LADDER[2])
                        self._base_hist.clear()
                        self._recent.clear()
                        self._last_change = now
                        self._state = "RECOVER"
                    continue

                # 上探
                if (now - self._last_change > 45 and base_avg > 0
                        and recent_avg >= base_avg * 0.85
                        and recent_avg < self._target_mbps * 1e6
                        and self._ladder_idx < len(LADDER) - 1):
                    new_idx = self._ladder_idx + 1
                    self.note(f"稳定（{recent_avg/1e3:.0f}KB/s ≥ 85% 基线）"
                              f"→ 上探 {LADDER[new_idx]}x")
                    self._ladder_idx = new_idx
                    self._apply_factor(LADDER[new_idx])
                    self._base_hist.clear()
                    self._base_hist.append(recent_avg)
                    self._last_change = now
                    self._state = "RAMP"
                elif recent_avg >= self._target_mbps * 1e6 * 0.9:
                    self._state = "HOLD"
                else:
                    self._state = f"HOLD（{factor}x）"

    def _apply_factor(self, f: float):
        with self._lock:
            for pid, st in self._states.items():
                sh = st.read()
                st.write(sh.enabled, DEFAULT_MASK, f, f, RATE_SAFE_FACTOR_B)
        self._manual_factor = f

    # ---------- 状态聚合 ----------
    def _prune_dead(self):
        """清掉已退出进程的注入状态。

        客户端会自己重启 baidunetdiskhost.exe，死掉的 pid 不清掉的话
        `injected` / 页面就会一直显示"已注入 N 个"这种假状态
        （实测踩到：明明一个都没注，页面报 injected=14）。
        """
        with self._lock:
            for pid in list(self._states.keys()):
                h = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                if h:
                    _k32.CloseHandle(ctypes.c_void_p(h))
                    continue
                try:
                    self._states.pop(pid).close()
                except OSError:
                    self._states.pop(pid, None)

    def _derive_state(self, health: dict, n_injected: int, enabled_now: bool) -> str:
        """人话状态：生效中 / 未生效。

        旧实现只在闭环线程里写 `_state`，而闭环线程**只在勾了「自动调速」时才启动**，
        所以手动设倍率时页面永远显示 IDLE——即使用户已经注入、hook 正在生效
        （用户实测报的就是这个）。改成从**实际 hook 健康度**推导；
        自动调速开启时它的状态（HOLD/BACKOFF/…）优先。
        """
        if self._auto and self._state and self._state != "IDLE":
            return self._state
        if n_injected == 0:
            return "未生效（未注入）"
        alive = sum(1 for v in health.values() if v.get("ok"))
        if alive == 0:
            return f"未生效（已注入 {n_injected} 个，DLL 未确认）"
        if not enabled_now:
            return f"已关闭加速（hook 保留，{alive} 个进程）"
        return f"生效中（{alive} 个进程）"

    def status(self) -> dict:
        self._prune_dead()
        pids = [p["pid"] for p in openspeedy.list_baidu_processes()]
        # 速度优先用网卡口径（不受预分配/本地校验读影响），读计数只兜底
        speed = self._nic.sample() if pids else 0.0
        if speed > 0:
            self._speed_src = "nic"
        elif pids:
            fb = self._sampler.sample(pids)
            if fb > 0:
                speed = fb
                self._speed_src = "read(兜底)"
        # 显示实际下发的倍率与开关（从任一共享内存读回）
        factor_now = self._manual_factor
        enabled_now = False
        health = {}
        with self._lock:
            for pid, st in self._states.items():
                try:
                    sh = st.read()
                    factor_now = sh.factorA1
                    enabled_now = bool(sh.enabled)
                except OSError:
                    pass
                try:
                    ok, why = st.dll_health()
                    health[pid] = {"ok": ok, "why": why}
                except OSError as e:
                    health[pid] = {"ok": False, "why": f"读取失败：{e}"}
            n_injected = len(self._states)
        return {
            "factor": factor_now,
            "enabled": enabled_now,
            "auto": self._auto,
            "target": self._target_mbps,
            "state": self._derive_state(health, n_injected, enabled_now),
            "speed": speed,
            "speed_src": self._speed_src,
            "win_sec": WIN_SEC,
            "injected": n_injected,
            "targets": self.targets(),
            "health": health,
            "log": list(self._log)[-12:],
            "server_cooldown": max(0.0, self._server_cooldown_until - time.time()),
        }

    def snapshot_log(self) -> list:
        return list(self._log)
