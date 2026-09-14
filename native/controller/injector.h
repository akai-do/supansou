// 注入器 + 进程发现 + 目标管理
#pragma once
#include <windows.h>
#include <string>
#include <vector>
#include <map>
#include "sharedmem.h"

namespace booster {

struct Target {
    unsigned long pid;
    std::wstring name;
    bool injected;      // 本控制器已知已注入
};

class Injector {
public:
    explicit Injector(const std::wstring& dllDir) : dll_dir_(dllDir) {}

    std::vector<Target> FindBaiduTargets() const;         // 白名单进程枚举
    bool IsInjected(unsigned long pid) const;             // 模块快照查 hook32/64
    bool HasOpenSpeedyHook(unsigned long pid) const;      // speedpatch 冲突检测
    bool HasModule(unsigned long pid, const wchar_t* module) const;  // 任意模块
    bool IsEngine(unsigned long pid) const;               // 加载 kernel.dll = 真下载引擎
    std::wstring Inject(unsigned long pid);               // 注入 + 建共享内存 + enable
    std::wstring Eject(unsigned long pid);                // 自卸载协议
    bool SetEnabled(unsigned long pid, bool enabled);
    bool SetFactor(unsigned long pid, double f);          // A1=A2=f, B=1
    bool GetStatus(unsigned long pid, BoosterShared& out) const;
    std::vector<unsigned long> InjectedPids() const;

    // watch 模式：轮询白名单进程，新进程自动注入；返回本次新增
    std::vector<unsigned long> WatchTick();

private:
    std::wstring DllPathFor(unsigned long pid) const;     // 按位数选 hook32/64
    bool IsWow64(unsigned long pid) const;                // 目标是否 32 位（WOW64）
    // 位数匹配的远程注入：交给同位数助手进程做 CreateRemoteThread
    std::wstring InjectViaHelper(unsigned long pid, const std::wstring& dll,
                                 unsigned long* helperExit) const;
    std::wstring ExeDir() const;

    std::wstring dll_dir_;
    std::map<unsigned long, SharedState> states_;
};

} // namespace booster
