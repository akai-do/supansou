// Booster 控制器 CLI（M1：list/inject/eject/enable/disable/setspeed/status/watch）
// M2 将在同一个 exe 上扩展：watch 自动调参闭环 + serve（本地 HTTP API）
#include <windows.h>
#include <stdio.h>
#include <string>
#include <vector>
#include "injector.h"
#include "sharedmem.h"

using namespace booster;

static void PrintTargets(Injector& inj) {
    auto targets = inj.FindBaiduTargets();
    wprintf(L"--- 百度网盘相关进程 ---\n");
    for (const Target& t : targets) {
        BoosterShared sh{};
        bool has = inj.GetStatus(t.pid, sh);
        wprintf(L"  pid=%-8lu %-28ls hook=%ls factor=%.1f enabled=%ls\n",
                t.pid, t.name.c_str(),
                t.injected ? L"是" : L"否",
                has ? sh.factorA1 : 0.0,
                has ? (sh.enabled ? L"开" : L"关") : L"-");
    }
    if (targets.empty()) wprintf(L"  （未发现百度网盘客户端进程）\n");
}

static std::vector<unsigned long> ResolvePids(Injector& inj, const std::wstring& which) {
    std::vector<unsigned long> out;
    if (_wcsicmp(which.c_str(), L"engine") == 0) {
        // 只挑真下载引擎（baidunetdiskhost.exe 且加载 kernel.dll）
        for (const Target& t : inj.FindBaiduTargets())
            if (_wcsicmp(t.name.c_str(), L"baidunetdiskhost.exe") == 0 &&
                inj.IsEngine(t.pid))
                out.push_back(t.pid);
    } else if (which == L"all" || which.empty()) {
        for (const Target& t : inj.FindBaiduTargets())
            out.push_back(t.pid);
    } else {
        out.push_back((unsigned long)_wtoi(which.c_str()));
    }
    return out;
}

int wmain(int argc, wchar_t** argv) {
    if (argc < 2) {
        wprintf(L"Booster 控制器（选择性时间 Hook）\n"
                L"用法：\n"
                L"  booster list                      查看目标进程与注入状态\n"
                L"  booster inject [pid|engine|all]   注入（engine=只注真下载引擎）\n"
                L"  booster eject <pid|all>           卸载 hook\n"
                L"  booster enable <pid|all>          启用加速\n"
                L"  booster disable <pid|all>         暂停加速（hook 保留）\n"
                L"  booster setspeed <倍率> [pid|all] 设置倍率（如 6.5）\n"
                L"  booster probe <周期ms|off> [pid|all] 自测节拍（off=零负载测量）\n"
                L"  booster status <pid>              读取共享状态\n"
                L"  booster watch [倍率]              常驻：只注真下载引擎 + 引擎重启后自动重挂\n");
        return 0;
    }

    Injector inj(L"");
    std::wstring cmd = argv[1];

    if (cmd == L"list") {
        PrintTargets(inj);
        return 0;
    }
    if (cmd == L"watch") {
        double f = argc >= 3 ? _wtof(argv[2]) : 0.0;
        wprintf(L"[watch] 常驻：只注入真下载引擎（带 kernel.dll 的 host）；"
                L"引擎进程变更后自动重挂%s\n",
                f > 0 ? L"" : L"（倍率沿用既有映射）");
        unsigned long last_engine = 0;
        while (true) {
            auto added = inj.WatchTick();
            for (unsigned long pid : added) {
                if (last_engine != 0 && pid != last_engine)
                    wprintf(L"[watch] 引擎进程已变更 %lu -> %lu，已重新注入\n",
                            last_engine, pid);
                else
                    wprintf(L"[watch] 引擎已注入 pid=%lu\n", pid);
                last_engine = pid;
            }
            if (f > 0)
                for (unsigned long pid : inj.InjectedPids())
                    inj.SetFactor(pid, f);
            Sleep(1000);
        }
        return 0;
    }
    if (cmd == L"inject") {
        auto pids = ResolvePids(inj, argc >= 3 ? argv[2] : L"all");
        for (unsigned long pid : pids)
            wprintf(L"[inject] %lu: %s\n", pid, inj.Inject(pid).c_str());
        return 0;
    }
    if (cmd == L"eject") {
        if (argc < 3) { wprintf(L"需要 pid 或 all\n"); return 1; }
        for (unsigned long pid : ResolvePids(inj, argv[2]))
            wprintf(L"[eject] %lu: %s\n", pid, inj.Eject(pid).c_str());
        return 0;
    }
    if (cmd == L"enable" || cmd == L"disable") {
        if (argc < 3) { wprintf(L"需要 pid 或 all\n"); return 1; }
        bool on = cmd == L"enable";
        for (unsigned long pid : ResolvePids(inj, argv[2]))
            wprintf(L"[%ls] %lu: %ls\n", cmd.c_str(), pid,
                    inj.SetEnabled(pid, on) ? L"OK" : L"失败（未注入）");
        return 0;
    }
    if (cmd == L"setspeed") {
        if (argc < 3) { wprintf(L"需要倍率数值\n"); return 1; }
        double f = _wtof(argv[2]);
        if (f < 1.0 || f > 64.0) { wprintf(L"倍率需在 1~64 之间\n"); return 1; }
        auto pids = ResolvePids(inj, argc >= 4 ? argv[3] : L"all");
        for (unsigned long pid : pids) {
            if (!inj.SetFactor(pid, f)) {
                // 未在映射中：尝试打开既有映射
                SharedState st;
                if (st.CreateOrOpen(pid)) {
                    BoosterShared v{};
                    st.Read(v);
                    v.factorA1 = v.factorA2 = f;
                    v.groupMask = GROUP_A1 | GROUP_A2 | GROUP_B;
                    if (v.probePeriodMs == 0) v.probePeriodMs = PROBE_PERIOD_DEFAULT;
                    st.Write(v);
                    wprintf(L"[setspeed] %lu: OK（打开既有映射）\n", pid);
                    continue;
                }
                wprintf(L"[setspeed] %lu: 失败（未注入）\n", pid);
                continue;
            }
            wprintf(L"[setspeed] %lu: %.2fx OK\n", pid, f);
        }
        return 0;
    }
    if (cmd == L"probe") {
        // probe <周期ms|off> [pid|all]：自测节拍开关。off = 零额外负载（测速场景）
        if (argc < 3) { wprintf(L"用法：booster probe <周期ms|off> [pid|all]\n"); return 1; }
        unsigned long long period = PROBE_PERIOD_DEFAULT;
        if (_wcsicmp(argv[2], L"off") == 0) {
            period = PROBE_OFF;
        } else {
            long long v = _wtoi64(argv[2]);
            if (v <= 0) { wprintf(L"周期需 > 0，或写 off\n"); return 1; }
            period = (unsigned long long)v;
        }
        auto pids = ResolvePids(inj, argc >= 4 ? argv[3] : L"all");
        for (unsigned long pid : pids) {
            SharedState st;
            if (!st.CreateOrOpen(pid)) {
                wprintf(L"[probe] %lu: 无共享状态（未注入）\n", pid);
                continue;
            }
            BoosterShared v{};
            st.Read(v);
            v.probePeriodMs = period;
            st.Write(v);
            wprintf(L"[probe] %lu: 自测节拍 %s\n", pid,
                    period == PROBE_OFF ? L"已关闭" : L"已设置");
        }
        return 0;
    }
    if (cmd == L"status") {
        if (argc < 3) { wprintf(L"需要 pid\n"); return 1; }
        unsigned long pid = (unsigned long)_wtoi(argv[2]);
        BoosterShared sh{};
        if (!inj.GetStatus(pid, sh)) {
            SharedState st;
            if (!st.CreateOrOpen(pid) || !st.Read(sh)) {
                wprintf(L"无共享状态（未注入或控制器未知）\n");
                return 1;
            }
        }
        wprintf(L"revision=%lu enabled=%lu mask=%lu F_A1=%.2f F_A2=%.2f F_B=%.2f "
                L"uninstall=%llu\n"
                L"  DLL: status=%llu detail=%llu sentinel=%llx actualA1=%.2f\n"
                L"  heartbeat=%llu stage2State=%llu winmmOk=%llu refreshCount=%llu "
                L"probeRatio=%.3f probeRealMs=%.1f qpcFactor=%.2f\n",
                sh.revision, sh.enabled, sh.groupMask, sh.factorA1, sh.factorA2,
                sh.factorB, sh.uninstall, sh.status, sh.detail, sh.sentinel,
                sh.actualA1, sh.heartbeat, sh.stage2State, sh.winmmOk,
                sh.refreshCount, sh.probeRatio, sh.probeRealMs, sh.qpcFactor);
        return 0;
    }
    wprintf(L"未知命令：%s\n", cmd.c_str());
    return 1;
}
