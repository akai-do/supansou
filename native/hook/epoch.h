// 共享状态读取 + 时钟族锚点（seqlock，单 epoch，因子变更 C0 连续无跳变）
#pragma once
#include <windows.h>
#include <atomic>
#include "booster_shared.h"

namespace booster {

// Hook 分组位常量：见 booster_shared.h（GROUP_A1 / GROUP_A2 / GROUP_B）

// ---- 控制器共享内存视图（DLL 侧只读） ----
bool EnsureSharedView(unsigned long pid);   // 打开/重试打开 DuPanBooster.<pid>
void RefreshIfChanged();                    // 共享 revision 变化时重载生效参数并重锚
bool ShouldUninstall();                     // 自卸载协议
bool HooksActive();                         // kernel32 hook 是否已成功安装

// ---- 生效参数（RefreshIfChanged 后有效） ----
struct Effective {
    bool enabled;
    unsigned long mask;
    double fA1, fA2, fB;
};
Effective GetEffective();

// ---- 时钟族：锚点 + 因子（读侧 seqlock 无锁；因子变更 C0 连续） ----
class ClockFamily {
public:
    void Init(unsigned long long realNow) {
        baseReal_ = realNow;
        baseVirt_ = realNow;
        factor_ = 1.0;
        seq_.store(0, std::memory_order_relaxed);
    }
    // 用当前锚点/因子推进到 realNow（读侧无锁）
    unsigned long long VirtualNow(unsigned long long realNow) const {
        unsigned long s1, s2;
        unsigned long long br, bv;
        double f;
        for (;;) {
            s1 = seq_.load(std::memory_order_acquire);
            if (s1 & 1) continue;
            br = baseReal_;
            bv = baseVirt_;
            f = factor_;
            s2 = seq_.load(std::memory_order_relaxed);
            if (s1 == s2) break;
        }
        return Advance(br, bv, f, realNow);
    }
    // 因子变更时重锚：virtual(now) 连续（写侧）
    void Rebase(unsigned long long realNow, double newFactor) {
        unsigned long long virt = Advance(baseReal_, baseVirt_, factor_, realNow);
        seq_.store(seq_.load(std::memory_order_relaxed) + 1, std::memory_order_relaxed);
        baseVirt_ = virt;
        baseReal_ = realNow;
        factor_ = newFactor;
        seq_.store(seq_.load(std::memory_order_relaxed) + 1, std::memory_order_release);
    }

    // 当前生效因子（回报自检用；读侧不加锁，仅用于展示）
    double Factor() const { return factor_; }

private:
    static unsigned long long Advance(unsigned long long baseReal,
                                      unsigned long long baseVirt,
                                      double f, unsigned long long realNow) {
        if (f == 1.0)
            return baseVirt + (realNow - baseReal);
        if (realNow >= baseReal)
            return baseVirt + (unsigned long long)((realNow - baseReal) * f);
        return baseVirt - (unsigned long long)((baseReal - realNow) * f);
    }

    std::atomic<unsigned long> seq_;
    unsigned long long baseReal_ = 0;
    unsigned long long baseVirt_ = 0;
    double factor_ = 1.0;
};

} // namespace booster
