// Booster 共享内存状态块（控制器写、hook DLL 读，seqlock 无锁协议）
// 命名：DuPanBooster.<pid>（控制器在注入前创建并持有句柄）
#pragma once
#include <windows.h>

namespace booster {

// Hook 分组位（与 Hook 决策表对应）
static const unsigned long GROUP_A1 = 1u << 0;  // 高精时钟：QPC / timeGetTime / PreciseFT
static const unsigned long GROUP_A2 = 1u << 1;  // 粗钟：GetTickCount(64) / SystemTimeAsFileTime
static const unsigned long GROUP_B  = 1u << 2;  // 睡眠等待：Sleep(Ex) / WaitFor*(Ex)（默认 factor=1 不缩放）

struct BoosterShared {
    volatile unsigned long seq;      // seqlock：偶数=稳定，奇数=写入中
    unsigned long revision;          // 每次控制器写入 +1（DLL 据此检测变更）
    unsigned long enabled;           // 0/1 总开关
    unsigned long groupMask;         // GROUP_A1|A2|B 组合
    double factorA1;                 // 高精时钟倍率
    double factorA2;                 // 粗钟倍率（可与 A1 独立关闭）
    double factorB;                  // 睡眠等待倍率（默认 1.0 = 透传）
    unsigned long long uninstall;    // 0/1 自卸载协议标志
    // requested：控制器请求的倍率；actual：DLL 实际生效的倍率（安装失败则保持请求值）
    double requestedA1;
    double actualA1;
    // 自检：DLL 写入的哨兵值，控制器据此确认 DLL 确实运行（0x424F4F53 = "BOOS"）
    unsigned long long sentinel;
    unsigned long long status;       // DLL 上报状态码（HOOK_STATUS_*）
    unsigned long long detail;       // 状态细节（如 MH_ERROR_* 或 GetLastError）
    // 心跳：监视线程每轮 +1。数值不再增长 = 线程已死/卡死（自检关键信号）
    unsigned long long heartbeat;
    unsigned long long monitorTicks; // 监视线程累计轮数（与 heartbeat 同源，便于对照）
    unsigned long long winmmOk;      // winmm timeGetTime hook 是否生效（0/1）
    unsigned long long stage2State;  // 阶段 2 细粒度进度（非 HOOK_STATUS_*，见 STAGE2_*）
    // DLL 自测：从 DLL 内部调用被 hook 的 QPC，量出 虚拟/真实 时钟比。
    // ≈ 生效倍率 → hook 确实生效；≈ 1.0 → 未被 hook 或已禁用
    double probeRatio;
    double probeRealMs;              // 自测用的真实窗口毫秒数
    // C 侧时钟族当前因子（真值，非推测）：应与 factorA1 一致
    double qpcFactor;
    double tickFactor;
    unsigned long long refreshCount; // RefreshIfChanged 实际重锚次数
    // 自测节拍（控制器可调）：0 = 用默认值（PROBE_PERIOD_DEFAULT）；
    // PROBE_OFF = 关闭自测（零额外负载，速率测量场景用）。
    unsigned long long probePeriodMs;
};

// 自测节拍常量（写进 BoosterShared::probePeriodMs）
// 默认 10s：保留"hook 是否真的生效"的直接证据，同时把自测的 1s 真实等待
// 摊薄到 ~10%，不再像原来那样每 2.5s 真实 Sleep 1s 干扰速率测量。
static const unsigned long long PROBE_PERIOD_DEFAULT = 10000;
static const unsigned long long PROBE_PERIOD_MIN     = 1000;   // 最短 1s（窗口 ≈333ms）
static const unsigned long long PROBE_OFF            = 0xFFFFFFFFULL;

// ---- 速率测量不变量（_diag_client_booster.py 会验证）----
// 只要目标是"比较真实吞吐"，就必须满足：
//   1) 只有 A 组（读时钟）被缩放，B 组（Sleep/WaitFor*）因子必须 = 1.0；
//   2) 否则 SelfProbe 里那次本意是"真实 1s"的 Sleep 会被 F 倍缩短，
//      probeRatio 会变成 F²，而且 F=1 的对照段本身也被加速 → 整组数据不可信。
static const unsigned long long RATE_SAFE_MASK = GROUP_A1 | GROUP_A2;
static const double RATE_SAFE_FACTOR_B = 1.0;

// 阶段 2 细粒度进度（写进 stage2State，用于定位 winmm 安装卡在哪一步）
static const unsigned long long STAGE2_BEGIN     = 1;   // 进入监视线程
static const unsigned long long STAGE2_WINMM_LOAD= 2;   // LoadLibrary/GetModuleHandle 成功
static const unsigned long long STAGE2_WINMM_RES = 3;   // GetProcAddress(timeGetTime) 成功
static const unsigned long long STAGE2_WINMM_CREATE = 4; // MH_CreateHook 成功
static const unsigned long long STAGE2_WINMM_ENABLE = 5; // MH_EnableHook 成功
static const unsigned long long STAGE2_LOOP      = 6;   // 进入监视循环

// DLL → 控制器 的状态码
static const unsigned long long HOOK_STATUS_NONE        = 0;   // 尚未上报
static const unsigned long long HOOK_STATUS_ENTERED     = 7;   // DLL 已附着，hook 尚未装完
static const unsigned long long HOOK_STATUS_OK          = 1;   // 全部 hook 安装成功
static const unsigned long long HOOK_STATUS_MH_INIT     = 2;   // MH_Initialize 失败
static const unsigned long long HOOK_STATUS_MH_CREATE   = 3;   // MH_CreateHook 失败
static const unsigned long long HOOK_STATUS_RESOLVE     = 4;   // GetProcAddress 解析失败
static const unsigned long long HOOK_STATUS_MH_ENABLE   = 5;   // MH_EnableHook 失败
static const unsigned long long HOOK_STATUS_RUNNING     = 6;   // 已运行（生效中）
static const unsigned long long HOOK_STATUS_WINMM_RES   = 8;   // winmm 解析/LoadLibrary 失败
static const unsigned long long HOOK_STATUS_EXCEPTION   = 9;   // 安装过程抛出 SEH 异常
static const unsigned long long HOOK_STATUS_NO_SHARED   = 10;  // 共享内存不可用（无控制面）

static const wchar_t* MapNameForPid(unsigned long pid, wchar_t* buf, size_t cch) {
    wsprintfW(buf, L"DuPanBooster.%lu", pid);
    return buf;
}

} // namespace booster
