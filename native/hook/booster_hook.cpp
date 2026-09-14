// Booster Hook DLL：选择性时间 Hook（分组倍率，超时/定时器默认透传）
//
// 与 OpenSpeedy 的差异：
//   - 分组倍率：A1 高精时钟 / A2 粗钟各自独立因子；B 睡眠等待默认 1.0 透传
//     （OpenSpeedy 全量缩放导致超时/重连/定时器失真 = 塌陷根因）
//   - 控制面：共享内存 seqlock（控制器写、DLL 读），revision 变化才重锚
//   - 单 epoch 派生：因子变更 C0 连续，无时间跳变
//   - INFINITE / 0 超时透传；缩放后为 0 时钳位到 1（防忙等）
//   - 事务式安装：全部 CreateHook 成功才统一 Enable；任一失败整体回滚
#include "hook_internal.h"
#include "epoch.h"
#include "booster_shared.h"
#include <MinHook.h>

namespace booster {

// ---------------- 真实函数指针 ----------------
PQPC_t            Real_QPC = nullptr;
PGetTickCount_t   Real_GetTickCount = nullptr;
PGetTickCount64_t Real_GetTickCount64 = nullptr;
PGetFT_t          Real_GetSystemTimeAsFileTime = nullptr;
PGetFT_t          Real_GetSystemTimePreciseAsFileTime = nullptr;
PtimeGetTime_t    Real_timeGetTime = nullptr;
PSleep_t          Real_Sleep = nullptr;
PSleepEx_t        Real_SleepEx = nullptr;
PW4SO_t           Real_WaitForSingleObject = nullptr;
PW4SOEx_t         Real_WaitForSingleObjectEx = nullptr;
PW4MO_t           Real_WaitForMultipleObjects = nullptr;
PW4MOEx_t         Real_WaitForMultipleObjectsEx = nullptr;

// ---------------- 生效参数 ----------------
static Effective g_eff = { false, 0, 1.0, 1.0, 1.0 };
static unsigned long g_revision = 0;
static BoosterShared* g_shared = nullptr;
static bool g_kernelHooksActive = false;
static bool g_winmmHookActive = false;
static HMODULE g_hSelf = nullptr;
static unsigned long g_pid = 0;
// 安装失败时记录具体环节，经共享内存回报给控制器（避免"注入了但没效果"的黑盒）
static unsigned long long g_lastStatus = HOOK_STATUS_NONE;
static unsigned long long g_lastDetail = 0;
// 自检心跳与阶段进度：控制器据此区分"线程卡死"和"根本没起来"
static volatile unsigned long long g_heartbeat = 0;
static volatile unsigned long long g_stage2 = 0;
static volatile bool g_winmmOk = false;
static volatile double g_probeRatio = 0.0;
static volatile double g_probeRealMs = 0.0;
static volatile unsigned long long g_refreshCount = 0;
// 自测节拍（控制器可调；默认 10s，可用 PROBE_OFF 关闭）
static volatile unsigned long long g_probePeriodMs = PROBE_PERIOD_DEFAULT;

Effective GetEffective() { return g_eff; }
bool HooksActive() { return g_kernelHooksActive; }

// ---- 各时钟族 ----
static ClockFamily g_csQPC;      // QPC ticks        （A1）
static ClockFamily g_csTick;     // 32 位 ms          （A2：GetTickCount/timeGetTime）
static ClockFamily g_csTick64;   // 64 位 ms          （A2：GetTickCount64）
static ClockFamily g_csFT;       // 100ns             （A2：SystemTimeAsFileTime/Precise）

// ---- 共享内存 seqlock 读 ----
static bool ReadShared(BoosterShared& out) {
    if (!g_shared) return false;
    unsigned long s1, s2;
    do {
        s1 = g_shared->seq;
        if (s1 & 1) return false;
        out = *g_shared;   // x86/x64 上对齐块拷贝在 seq 校验下安全
        s2 = g_shared->seq;
    } while (s1 != s2);
    return true;
}

// 共享 revision 变化 → 重载生效参数并重锚各族
void RefreshIfChanged() {
    if (!g_shared) return;
    unsigned long rev = g_shared->revision;   // 热路径：单次对齐读
    if (rev == g_revision) return;

    BoosterShared sh;
    if (!ReadShared(sh)) return;

    g_eff.enabled = sh.enabled != 0;
    g_eff.mask = sh.groupMask;
    g_eff.fA1 = sh.factorA1;
    g_eff.fA2 = sh.factorA2;
    g_eff.fB = sh.factorB;

    // 关键：hook 尚未安装时不消费 revision。
    // 阶段 1（DllMain）注入时就会调到这里，此时 g_kernelHooksActive 还是 false；
    // 若在这里把 g_revision 推进，阶段 2 装好 hook 后再调用会因"revision 未变化"
    // 直接 return，导致 hook 装上了但因子永远是 1.0（历史上正是这个坑）。
    if (!g_kernelHooksActive) return;
    g_revision = rev;
    g_refreshCount++;

    double a1 = (g_eff.enabled && (g_eff.mask & GROUP_A1)) ? g_eff.fA1 : 1.0;
    double a2 = (g_eff.enabled && (g_eff.mask & GROUP_A2)) ? g_eff.fA2 : 1.0;

    LARGE_INTEGER qpc;
    if (Real_QPC && Real_QPC(&qpc))
        g_csQPC.Rebase((unsigned long long)qpc.QuadPart, a1);
    if (Real_GetTickCount64)
        g_csTick64.Rebase(Real_GetTickCount64(), a2);
    g_csTick.Rebase(Real_GetTickCount(), a2);
    if (Real_GetSystemTimeAsFileTime) {
        FILETIME ft;
        Real_GetSystemTimeAsFileTime(&ft);
        g_csFT.Rebase(((unsigned long long)ft.dwHighDateTime << 32) | ft.dwLowDateTime, a2);
    }
}

// ---------------- 因子门控 ----------------
static double FA1() {
    return (g_eff.enabled && (g_eff.mask & GROUP_A1)) ? g_eff.fA1 : 1.0;
}
static double FA2() {
    return (g_eff.enabled && (g_eff.mask & GROUP_A2)) ? g_eff.fA2 : 1.0;
}
static double FB() {
    return (g_eff.enabled && (g_eff.mask & GROUP_B)) ? g_eff.fB : 1.0;
}
// 超时缩放：透传 INFINITE/0；缩到 0 钳位 1；绝不放大
static DWORD ScaleWait(DWORD ms, double f) {
    if (f <= 1.0 || ms == 0 || ms == INFINITE) return ms;
    double scaled = (double)ms / f;
    if (scaled <= 0.0) return 1;
    DWORD out = (DWORD)scaled;
    if (out == 0) out = 1;
    return out > ms ? ms : out;
}

// ---------------- Hook 实现 ----------------
static BOOL WINAPI Hooked_QPC(LARGE_INTEGER* lp) {
    RefreshIfChanged();
    LARGE_INTEGER now;
    if (!Real_QPC(&now) || !lp) return FALSE;
    lp->QuadPart = (LONGLONG)g_csQPC.VirtualNow((unsigned long long)now.QuadPart);
    return TRUE;
}

static DWORD WINAPI Hooked_GetTickCount(void) {
    RefreshIfChanged();
    return (DWORD)g_csTick.VirtualNow(Real_GetTickCount());
}

static ULONGLONG WINAPI Hooked_GetTickCount64(void) {
    RefreshIfChanged();
    return g_csTick64.VirtualNow(Real_GetTickCount64());
}

static void WINAPI Hooked_GetSystemTimeAsFileTime(LPFILETIME ft) {
    RefreshIfChanged();
    FILETIME now;
    Real_GetSystemTimeAsFileTime(&now);
    unsigned long long v = g_csFT.VirtualNow(
        ((unsigned long long)now.dwHighDateTime << 32) | now.dwLowDateTime);
    if (ft) {
        ft->dwLowDateTime = (DWORD)v;
        ft->dwHighDateTime = (DWORD)(v >> 32);
    }
}

static void WINAPI Hooked_GetSystemTimePreciseAsFileTime(LPFILETIME ft) {
    RefreshIfChanged();
    FILETIME now;
    if (Real_GetSystemTimePreciseAsFileTime)
        Real_GetSystemTimePreciseAsFileTime(&now);
    else
        Real_GetSystemTimeAsFileTime(&now);
    unsigned long long v = g_csFT.VirtualNow(
        ((unsigned long long)now.dwHighDateTime << 32) | now.dwLowDateTime);
    if (ft) {
        ft->dwLowDateTime = (DWORD)v;
        ft->dwHighDateTime = (DWORD)(v >> 32);
    }
}

static DWORD WINAPI Hooked_timeGetTime(void) {
    RefreshIfChanged();
    return (DWORD)g_csTick.VirtualNow(Real_timeGetTime());
}

static void WINAPI Hooked_Sleep(DWORD ms) {
    RefreshIfChanged();
    Real_Sleep(ScaleWait(ms, FB()));
}

static DWORD WINAPI Hooked_SleepEx(DWORD ms, BOOL alertable) {
    RefreshIfChanged();
    return Real_SleepEx(ScaleWait(ms, FB()), alertable);
}

static DWORD WINAPI Hooked_WaitForSingleObject(HANDLE h, DWORD ms) {
    RefreshIfChanged();
    return Real_WaitForSingleObject(h, ScaleWait(ms, FB()));
}

static DWORD WINAPI Hooked_WaitForSingleObjectEx(HANDLE h, DWORD ms, BOOL alertable) {
    RefreshIfChanged();
    return Real_WaitForSingleObjectEx(h, ScaleWait(ms, FB()), alertable);
}

static DWORD WINAPI Hooked_WaitForMultipleObjects(DWORD n, const HANDLE* hs,
                                                  BOOL waitAll, DWORD ms) {
    RefreshIfChanged();
    return Real_WaitForMultipleObjects(n, hs, waitAll, ScaleWait(ms, FB()));
}

static DWORD WINAPI Hooked_WaitForMultipleObjectsEx(DWORD n, const HANDLE* hs,
                                                    BOOL waitAll, DWORD ms,
                                                    BOOL alertable) {
    RefreshIfChanged();
    return Real_WaitForMultipleObjectsEx(n, hs, waitAll,
                                         ScaleWait(ms, FB()), alertable);
}

// ---------------- 安装 ----------------
bool InstallKernelHooks() {
    if (g_kernelHooksActive) return true;
    HMODULE k32 = GetModuleHandleW(L"kernel32.dll");
    if (!k32) return false;

    PQPC_t qpc = (PQPC_t)GetProcAddress(k32, "QueryPerformanceCounter");
    PGetTickCount_t gtc = (PGetTickCount_t)GetProcAddress(k32, "GetTickCount");
    PGetTickCount64_t gtc64 = (PGetTickCount64_t)GetProcAddress(k32, "GetTickCount64");
    PGetFT_t gft = (PGetFT_t)GetProcAddress(k32, "GetSystemTimeAsFileTime");
    PGetFT_t gftp = (PGetFT_t)GetProcAddress(k32, "GetSystemTimePreciseAsFileTime");
    PSleep_t sleep_ = (PSleep_t)GetProcAddress(k32, "Sleep");
    PSleepEx_t sleepEx = (PSleepEx_t)GetProcAddress(k32, "SleepEx");
    PW4SO_t w4so = (PW4SO_t)GetProcAddress(k32, "WaitForSingleObject");
    PW4SOEx_t w4soEx = (PW4SOEx_t)GetProcAddress(k32, "WaitForSingleObjectEx");
    PW4MO_t w4mo = (PW4MO_t)GetProcAddress(k32, "WaitForMultipleObjects");
    PW4MOEx_t w4moEx = (PW4MOEx_t)GetProcAddress(k32, "WaitForMultipleObjectsEx");
    if (!qpc || !gtc || !gtc64 || !gft || !sleep_ || !sleepEx ||
        !w4so || !w4soEx || !w4mo || !w4moEx)
        return false;

    Real_QPC = qpc;
    Real_GetTickCount = gtc;
    Real_GetTickCount64 = gtc64;
    Real_GetSystemTimeAsFileTime = gft;
    Real_GetSystemTimePreciseAsFileTime = gftp;   // 可为空（Win7）；Hook 内兜底
    Real_Sleep = sleep_;
    Real_SleepEx = sleepEx;
    Real_WaitForSingleObject = w4so;
    Real_WaitForSingleObjectEx = w4soEx;
    Real_WaitForMultipleObjects = w4mo;
    Real_WaitForMultipleObjectsEx = w4moEx;

    LARGE_INTEGER q;
    qpc(&q);
    g_csQPC.Init((unsigned long long)q.QuadPart);
    g_csTick.Init((unsigned long long)gtc());
    if (gtc64) g_csTick64.Init(gtc64());
    FILETIME ft;
    gft(&ft);
    g_csFT.Init(((unsigned long long)ft.dwHighDateTime << 32) | ft.dwLowDateTime);

    struct Entry { void* target; void* detour; void** real; };
    Entry entries[] = {
        { (void*)qpc,    (void*)Hooked_QPC,    (void**)&Real_QPC },
        { (void*)gtc,    (void*)Hooked_GetTickCount, (void**)&Real_GetTickCount },
        { (void*)gtc64,  (void*)Hooked_GetTickCount64, (void**)&Real_GetTickCount64 },
        { (void*)gft,    (void*)Hooked_GetSystemTimeAsFileTime, (void**)&Real_GetSystemTimeAsFileTime },
        { (void*)sleep_, (void*)Hooked_Sleep,  (void**)&Real_Sleep },
        { (void*)sleepEx,(void*)Hooked_SleepEx,(void**)&Real_SleepEx },
        { (void*)w4so,   (void*)Hooked_WaitForSingleObject, (void**)&Real_WaitForSingleObject },
        { (void*)w4soEx, (void*)Hooked_WaitForSingleObjectEx, (void**)&Real_WaitForSingleObjectEx },
        { (void*)w4mo,   (void*)Hooked_WaitForMultipleObjects, (void**)&Real_WaitForMultipleObjects },
        { (void*)w4moEx, (void*)Hooked_WaitForMultipleObjectsEx, (void**)&Real_WaitForMultipleObjectsEx },
    };

    if (MH_Initialize() != MH_OK) {
        g_lastStatus = HOOK_STATUS_MH_INIT;
        return false;
    }
    int created = 0;
    for (const Entry& e : entries) {
        MH_STATUS s = MH_CreateHook(e.target, e.detour, e.real);
        if (s != MH_OK) {
            g_lastStatus = HOOK_STATUS_MH_CREATE;
            g_lastDetail = (unsigned long long)s;
            for (int i = created - 1; i >= 0; --i)   // 回滚
                MH_RemoveHook(entries[i].target);
            MH_Uninitialize();
            return false;
        }
        created++;
    }
    for (const Entry& e : entries) {
        MH_STATUS s = MH_EnableHook(e.target);
        if (s != MH_OK) {
            g_lastStatus = HOOK_STATUS_MH_ENABLE;
            g_lastDetail = (unsigned long long)s;
        }
    }
    g_kernelHooksActive = true;
    // hook 刚生效：立刻应用一次共享内存里的当前参数（否则要等下一次 revision 变化）
    g_revision = 0;
    g_lastStatus = HOOK_STATUS_OK;
    RefreshIfChanged();
    if (g_refreshCount == 0) {
        // 共享内存不可读（控制器尚未写入）：先按 1.0 透明运行，等 revision 到达
        g_lastStatus = HOOK_STATUS_OK;
    }
    return true;
}

bool InstallWinmmHook() {
    if (g_winmmHookActive) return true;
    HMODULE winmm = GetModuleHandleW(L"winmm.dll");
    if (!winmm) winmm = LoadLibraryW(L"winmm.dll");
    if (!winmm) return false;
    PtimeGetTime_t tgt = (PtimeGetTime_t)GetProcAddress(winmm, "timeGetTime");
    if (!tgt) return false;
    if (Real_timeGetTime == nullptr) Real_timeGetTime = tgt;
    if (MH_CreateHook((void*)tgt, (void*)Hooked_timeGetTime,
                      (void**)&Real_timeGetTime) != MH_OK)
        return false;
    if (MH_EnableHook((void*)tgt) != MH_OK) return false;
    g_winmmHookActive = true;
    return true;
}

// ---------------- 共享内存视图 + 自卸载监视 ----------------
static bool EnsureSharedView() {
    if (g_shared) return true;
    wchar_t name[64];
    MapNameForPid(g_pid, name, 64);
    // 需要写权限：DLL 要回报 status/sentinel/actual 供控制器自检
    HANDLE map = OpenFileMappingW(FILE_MAP_ALL_ACCESS, FALSE, name);
    if (!map) return false;
    g_shared = (BoosterShared*)MapViewOfFile(map, FILE_MAP_ALL_ACCESS, 0, 0,
                                             sizeof(BoosterShared));
    return g_shared != nullptr;
}

// 向控制器回报状态（DLL 侧唯一写入点，与控制器 seqlock 写互不重叠）
static void ReportStatus(unsigned long long status, unsigned long long detail) {
    if (!g_shared) return;
    g_shared->sentinel = 0x424F4F53ULL;   // "BOOS"：证明 DLL 真的在跑
    g_shared->status = status;
    g_shared->detail = detail;
    g_shared->heartbeat = ++g_heartbeat;
    g_shared->stage2State = g_stage2;
    g_shared->winmmOk = g_winmmOk ? 1 : 0;
    g_shared->probeRatio = g_probeRatio;
    g_shared->probeRealMs = g_probeRealMs;
    g_shared->qpcFactor = g_csQPC.Factor();
    g_shared->tickFactor = g_csTick.Factor();
    // actualA1 必须反映 DLL 内部真正生效的因子（不是共享内存里的请求值）
    g_shared->actualA1 = (g_eff.enabled && (g_eff.mask & GROUP_A1)) ? g_eff.fA1 : 1.0;
    g_shared->refreshCount = g_refreshCount;
}

// DLL 自测：用「被 hook 的 QPC」量虚拟时长，用「未被 hook 的 GetSystemTime」量真实
// 时长，比值 = 真正生效的倍率。这是"hook 到底有没有生效"的直接证据。
// GetSystemTime 不在 hook 名单里（只 hook 了 GetSystemTimeAsFileTime/Precise），
// 所以它能给出未经缩放的墙钟参考——只有 ~15ms 粒度，窗口越长越准。
//
// 节拍由控制器经 probePeriodMs 调节；窗口固定 1s（不受节拍影响），
// 只有"多久测一次"变。1s 窗口配合 15.6ms 的 SYSTEMTIME 粒度 ≈ ±1.6% 误差。
static const DWORD PROBE_WINDOW_MS = 1000;

static void SelfProbe() {
    static HMODULE k32 = GetModuleHandleW(L"kernel32.dll");
    if (!k32) return;
    typedef BOOL(WINAPI * Qpc_t)(LARGE_INTEGER*);
    typedef void(WINAPI * Gst_t)(LPSYSTEMTIME);
    // 必须用 GetProcAddress 取入口地址：MinHook 改写的就是这个导出的内存
    Qpc_t qpc = (Qpc_t)GetProcAddress(k32, "QueryPerformanceCounter");
    Gst_t gst = (Gst_t)GetProcAddress(k32, "GetSystemTime");
    if (!qpc || !gst || Real_QPC == nullptr) return;
    LARGE_INTEGER freq{};
    if (!QueryPerformanceFrequency(&freq) || freq.QuadPart == 0) return;

    unsigned long long period = g_probePeriodMs;
    if (period == 0 || period > PROBE_PERIOD_DEFAULT) period = PROBE_PERIOD_DEFAULT;
    DWORD windowMs = period < PROBE_WINDOW_MS ? (DWORD)period : PROBE_WINDOW_MS;
    if (windowMs < 333) windowMs = 333;

    auto toMs = [](const SYSTEMTIME& s) -> double {
        return ((double)s.wHour * 3600.0 + s.wMinute * 60.0 + s.wSecond) * 1000.0
               + s.wMilliseconds;
    };

    SYSTEMTIME s0{}, s1{};
    LARGE_INTEGER q0{}, q1{};
    gst(&s0);
    qpc(&q0);                 // 走 hook → 虚拟时钟
    Sleep(windowMs);          // GROUP_B 默认 factor=1 → 真实等待（同时验证透传）
    qpc(&q1);
    gst(&s1);

    double realMs = toMs(s1) - toMs(s0);
    if (realMs < 0) realMs += 86400.0 * 1000.0;          // 跨零点
    if (realMs < (double)windowMs * 0.5) {               // 明显被吞的样本丢弃
        g_probeRealMs = realMs;
        return;
    }
    double virtMs = (double)(q1.QuadPart - q0.QuadPart) * 1000.0
                    / (double)freq.QuadPart;
    g_probeRealMs = realMs;
    g_probeRatio = realMs > 1.0 ? virtMs / realMs : 0.0;
}

static DWORD WINAPI MonitorThread(LPVOID) {
    g_stage2 = STAGE2_BEGIN;
    __try {
        // 阶段 2：winmm timeGetTime（DllMain 里不能 LoadLibrary）
        if (EnsureSharedView()) {
            ReportStatus(HOOK_STATUS_ENTERED, 0);
            g_stage2 = STAGE2_WINMM_LOAD;
            bool w = InstallWinmmHook();
            g_winmmOk = w;
            if (!w && g_kernelHooksActive) g_lastStatus = HOOK_STATUS_WINMM_RES;
            ReportStatus(g_kernelHooksActive ? HOOK_STATUS_OK : g_lastStatus,
                         g_lastDetail);
        } else {
            g_lastStatus = HOOK_STATUS_NO_SHARED;
        }
        g_stage2 = STAGE2_LOOP;
        // 自测调度改成"按真实经过时间"，不再按轮数：
        // 轮数受 Sleep(300) 抖动量级影响，而 probePeriodMs(可调) 才是控制面承诺的节拍。
        unsigned long long lastProbeTick = GetTickCount64();
        while (true) {
            if (EnsureSharedView()) {
                RefreshIfChanged();
                unsigned long long now = GetTickCount64();
                unsigned long long period = g_probePeriodMs;
                if (period != PROBE_OFF && now - lastProbeTick >= period) {
                    lastProbeTick = now;
                    SelfProbe();
                }
                ReportStatus(g_kernelHooksActive ? HOOK_STATUS_OK : g_lastStatus,
                             g_lastDetail);
            }
            if (ShouldUninstall()) {
                if (g_kernelHooksActive) {
                    MH_DisableHook(MH_ALL_HOOKS);
                    Sleep(200);
                    MH_Uninitialize();
                }
                HANDLE self = g_hSelf;
                FreeLibraryAndExitThread((HMODULE)self, 0);
            }
            Sleep(300);
        }
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        g_lastStatus = HOOK_STATUS_EXCEPTION;
        g_lastDetail = (unsigned long long)GetExceptionCode();
        if (g_shared) ReportStatus(g_lastStatus, g_lastDetail);
        return 0;
    }
    return 0;
}

bool ShouldUninstall() {
    BoosterShared sh;
    if (!ReadShared(sh)) return false;
    return sh.uninstall != 0;
}

DWORD WINAPI MonitorThreadExported(LPVOID) { MonitorThread(nullptr); return 0; }

bool InitBoosterDllStage1(HMODULE hSelf) {
    g_hSelf = hSelf;
    g_pid = GetCurrentProcessId();
    EnsureSharedView();      // 控制器注入前已创建；未创建时监视线程会重试
    RefreshIfChanged();
    return InstallKernelHooks();
}

} // namespace booster
