"""系统级网卡收包计数（M2 闭环与对照实验共用的"第三信号"）。

为什么需要它：闭环用 openspeedy.SpeedSampler（GetProcessIoCounters.ReadTransferCount）
当速度源，但实测这个计数**把网络读与本地文件读混算**，与落盘量/网卡量可以差
9.6~40 倍（客户端预分配/本地秒传会让落盘虚高，引擎校验本地文件会让读计数虚高）。
用它驱动调参，会出现"实际在高速下载、闭环却看到 13KB/s 然后一直降档"。

本模块用 iphlpapi 的 GetIfTable（不引第三方依赖，本机 psutil 不可用）读网卡收包。

两个实测坑（2026-09-13 本机）：
  1. **同一块物理网卡的流量会在多个接口上镜像出现**（实测 4 个接口的 InOctets
     完全相同），所以只能取**单接口**，求和会把收包量放大约 5 倍。
  2. **必须钉住同一块接口**（pin）：每样本重新取最大值会在接口之间跳变，
     序列出现断点，会被误判成"衰减"。
  3. 绝对值比应用层字节高约 25%（协议头 + 重传；两次实测 24.4% / 26.3%），
     所以适合做**比值/相对趋势**判据，绝对值只作量级参考。

已用已知大小的下载校验过：人为把速率压到约 1/5 后，本计数给出的
首窗/末窗比值能正确反映衰减。
"""
import ctypes
import sys

# 网卡计数用 iphlpapi（Windows 专有）。本模块被跨平台的 accel 蓝图导入，
# 所以**导入必须安全**：非 Windows 上没有 ctypes.windll，直接访问会让整个
# 后端起不来（实测：部署到 Linux 服务器时崩在 import 阶段）。
IS_WINDOWS = sys.platform.startswith("win")

IF_TYPE_SOFTWARE_LOOPBACK = 24
MAX_INTERFACE_NAME_LEN = 256
MAXLEN_PHYSADDR = 8
MAXLEN_IFDESCR = 256
DWORD_MOD = 1 << 32


class MIB_IFROW(ctypes.Structure):
    _fields_ = [("wszName", ctypes.c_wchar * MAX_INTERFACE_NAME_LEN),
                ("dwIndex", ctypes.c_ulong),
                ("dwType", ctypes.c_ulong),
                ("dwMtu", ctypes.c_ulong),
                ("dwSpeed", ctypes.c_ulong),
                ("dwPhysAddrLen", ctypes.c_ulong),
                ("bPhysAddr", ctypes.c_ubyte * MAXLEN_PHYSADDR),
                ("dwAdminStatus", ctypes.c_ulong),
                ("dwOperStatus", ctypes.c_ulong),
                ("dwLastChange", ctypes.c_ulong),
                ("dwInOctets", ctypes.c_ulong),
                ("dwOutOctets", ctypes.c_ulong),
                ("dwInUcastPkts", ctypes.c_ulong),
                ("dwInNUcastPkts", ctypes.c_ulong),
                ("dwInDiscards", ctypes.c_ulong),
                ("dwInErrors", ctypes.c_ulong),
                ("dwInUnknownProtos", ctypes.c_ulong),
                ("dwOutUcastPkts", ctypes.c_ulong),
                ("dwOutNUcastPkts", ctypes.c_ulong),
                ("dwOutDiscards", ctypes.c_ulong),
                ("dwOutErrors", ctypes.c_ulong),
                ("dwOutQLen", ctypes.c_ulong),
                ("dwDescrLen", ctypes.c_ulong),
                ("bDescr", ctypes.c_ubyte * MAXLEN_IFDESCR)]


_iphlpapi = ctypes.windll.iphlpapi if IS_WINDOWS else None


def if_rows():
    """{dwIndex: (dwType, dwInOctets)}；失败/非 Windows 返回 None。"""
    if _iphlpapi is None:
        return None
    size = ctypes.c_ulong(0)
    _iphlpapi.GetIfTable(None, ctypes.byref(size), False)
    if size.value <= ctypes.sizeof(ctypes.c_ulong):
        return None
    buf = ctypes.create_string_buffer(size.value)
    if _iphlpapi.GetIfTable(ctypes.byref(buf), ctypes.byref(size), False) != 0:
        return None
    n = ctypes.cast(buf, ctypes.POINTER(ctypes.c_ulong)).contents.value
    rows = ctypes.cast(ctypes.byref(buf, ctypes.sizeof(ctypes.c_ulong)),
                       ctypes.POINTER(MIB_IFROW))
    out = {}
    for i in range(min(n, 256)):
        r = rows[i]
        out[int(r.dwIndex)] = (int(r.dwType), int(r.dwInOctets))
    return out


class NicRxCounter:
    """钉住一块非回环接口，返回它的累计收包字节（含 DWORD 回绕补偿）。"""

    def __init__(self):
        self._last = {}
        self._acc = {}
        self._pinned = None
        self.pinned_index = None

    def pin(self):
        """钉住当前最忙的非回环接口并建立基线。返回接口索引或 None。"""
        rows = if_rows()
        if not rows:
            return None
        cand = [(ino, idx) for idx, (typ, ino) in rows.items()
                if typ != IF_TYPE_SOFTWARE_LOOPBACK]
        if not cand:
            return None
        self._pinned = max(cand)[1]
        self.pinned_index = self._pinned
        self._last = {self._pinned: rows[self._pinned][1]}
        self._acc = {self._pinned: 0}
        return self._pinned

    def sample(self):
        if self._pinned is None:
            if self.pin() is None:
                return None
        rows = if_rows()
        if rows is None or self._pinned not in rows:
            return None
        inoctets = rows[self._pinned][1]
        prev = self._last.get(self._pinned)
        acc = self._acc.get(self._pinned, 0)
        if prev is not None:
            d = inoctets - prev
            if d < 0:
                d += DWORD_MOD
            acc += d
        self._last[self._pinned] = inoctets
        self._acc[self._pinned] = acc
        return acc


class NicSpeedSampler:
    """与 openspeedy.SpeedSampler 同接口（sample() -> 字节/秒），但走网卡。

    用网卡当速度源可以在客户端预分配/本地秒传时仍然看到真实下载量，
    避免闭环"看错速度一直降档"。取不到网卡信息时返回 0，调用方可回退。
    """

    def __init__(self):
        self._c = NicRxCounter()
        self._last = None
        self._last_t = None

    @property
    def pinned_index(self):
        return self._c.pinned_index

    def sample(self):
        import time as _t
        cur = self._c.sample()
        now = _t.time()
        if cur is None:
            return 0.0
        speed = 0.0
        if self._last is not None and self._last_t is not None and now > self._last_t:
            d = cur - self._last
            if d < 0:
                d += DWORD_MOD
            speed = d / (now - self._last_t)
        self._last, self._last_t = cur, now
        return max(0.0, speed)
