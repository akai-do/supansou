#include "injector.h"
#include <tlhelp32.h>
#include <strsafe.h>

namespace booster {

static const wchar_t* kWhitelist[] = {
    L"BaiduNetdisk.exe", L"BaiduNetdiskUnite.exe", L"baidunetdiskhost.exe",
};
static const wchar_t* kOurDll32 = L"hook32.dll";
static const wchar_t* kOurDll64 = L"hook64.dll";
static const wchar_t* kSpeedPatch[] = { L"speedpatch32.dll", L"speedpatch64.dll" };
// 真下载引擎的标志模块（见 IsEngine 注释）
static const wchar_t* kEngineModule = L"kernel.dll";
static const wchar_t* kEngineProcess = L"baidunetdiskhost.exe";

std::vector<Target> Injector::FindBaiduTargets() const {
    std::vector<Target> out;
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snap == INVALID_HANDLE_VALUE) return out;
    PROCESSENTRY32W pe{};
    pe.dwSize = sizeof(pe);
    if (Process32FirstW(snap, &pe)) {
        do {
            for (const wchar_t* w : kWhitelist) {
                if (_wcsicmp(pe.szExeFile, w) == 0) {
                    out.push_back({ pe.th32ProcessID, std::wstring(w), false });
                    break;
                }
            }
        } while (Process32NextW(snap, &pe));
    }
    CloseHandle(snap);
    for (auto& t : out)
        t.injected = IsInjected(t.pid);
    return out;
}

bool Injector::IsInjected(unsigned long pid) const {
    HANDLE snap = CreateToolhelp32Snapshot(
        TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid);
    if (snap == INVALID_HANDLE_VALUE) return false;
    bool found = false;
    MODULEENTRY32W me{};
    me.dwSize = sizeof(me);
    if (Module32FirstW(snap, &me)) {
        do {
            if (_wcsicmp(me.szModule, kOurDll32) == 0 ||
                _wcsicmp(me.szModule, kOurDll64) == 0) {
                found = true;
                break;
            }
        } while (Module32NextW(snap, &me));
    }
    CloseHandle(snap);
    return found;
}

bool Injector::HasOpenSpeedyHook(unsigned long pid) const {
    HANDLE snap = CreateToolhelp32Snapshot(
        TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid);
    if (snap == INVALID_HANDLE_VALUE) return false;
    bool found = false;
    MODULEENTRY32W me{};
    me.dwSize = sizeof(me);
    if (Module32FirstW(snap, &me)) {
        do {
            for (const wchar_t* w : kSpeedPatch) {
                if (_wcsicmp(me.szModule, w) == 0) { found = true; break; }
            }
        } while (Module32NextW(snap, &me) && !found);
    }
    CloseHandle(snap);
    return found;
}

bool Injector::HasModule(unsigned long pid, const wchar_t* module) const {
    HANDLE snap = CreateToolhelp32Snapshot(
        TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid);
    if (snap == INVALID_HANDLE_VALUE) return false;
    bool found = false;
    MODULEENTRY32W me{};
    me.dwSize = sizeof(me);
    if (Module32FirstW(snap, &me)) {
        do {
            if (_wcsicmp(me.szModule, module) == 0) { found = true; break; }
        } while (Module32NextW(snap, &me));
    }
    CloseHandle(snap);
    return found;
}

// 真下载引擎 = baidunetdiskhost.exe 且加载了 kernel.dll（qingluan 下载模块）。
// 同名进程有两个，另一个只加载 vastplayer.dll（视频插件），对它注入毫无作用——
// 只按进程名判定会重现 V1/V2 那个"注入成功但倍率无效"的假象。
bool Injector::IsEngine(unsigned long pid) const {
    return HasModule(pid, kEngineModule);
}

std::wstring Injector::ExeDir() const {
    wchar_t path[MAX_PATH]{};
    GetModuleFileNameW(nullptr, path, MAX_PATH);
    std::wstring p(path);
    size_t pos = p.find_last_of(L"\\/");
    return pos == std::wstring::npos ? L"." : p.substr(0, pos);
}

bool Injector::IsWow64(unsigned long pid) const {
    HANDLE h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
    if (!h) return false;
    BOOL wow64 = FALSE;
    typedef BOOL(WINAPI* IsWow64_t)(HANDLE, PBOOL);
    IsWow64_t fn = (IsWow64_t)GetProcAddress(
        GetModuleHandleW(L"kernel32.dll"), "IsWow64Process");
    bool is32 = false;
    if (fn && fn(h, &wow64)) is32 = wow64 != FALSE;
    CloseHandle(h);
    return is32;
}

std::wstring Injector::DllPathFor(unsigned long pid) const {
    return ExeDir() + L"\\" + (IsWow64(pid) ? kOurDll32 : kOurDll64);
}

// 位数匹配的远程注入：x64 进程里的 LoadLibraryW 地址在 WOW64 目标里是无效的，
// 所以 32 位目标必须由 32 位助手进程去 CreateRemoteThread。
std::wstring Injector::InjectViaHelper(unsigned long pid, const std::wstring& dll,
                                       unsigned long* helperExit) const {
    const wchar_t* helperName = IsWow64(pid) ? L"inject32.exe" : L"inject64.exe";
    std::wstring helper = ExeDir() + L"\\" + helperName;
    if (GetFileAttributesW(helper.c_str()) == INVALID_FILE_ATTRIBUTES)
        return std::wstring(L"MISSING:") + helper;

    std::wstring cmd = L"\"" + helper + L"\" " + std::to_wstring(pid) +
                       L" \"" + dll + L"\"";
    std::vector<wchar_t> buf(cmd.begin(), cmd.end());
    buf.push_back(L'\0');

    STARTUPINFOW si{};
    si.cb = sizeof(si);
    PROCESS_INFORMATION pi{};
    if (!CreateProcessW(nullptr, buf.data(), nullptr, nullptr, FALSE,
                        CREATE_NO_WINDOW, nullptr, nullptr, &si, &pi))
        return L"CreateProcess(helper) 失败";
    WaitForSingleObject(pi.hProcess, 15000);
    DWORD code = 0;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    if (helperExit) *helperExit = code;
    return code == 0 ? std::wstring() : std::wstring(L"helper-exit-code");
}

std::wstring Injector::Inject(unsigned long pid) {
    wchar_t msg[256]{};

    // 冲突检测：OpenSpeedy 的 hook 与本工具的 MinHook 不能同进程共存
    if (HasOpenSpeedyHook(pid)) {
        StringCchPrintfW(msg, 256,
            L"该进程已加载 OpenSpeedy 的 speedpatch（两套 hook 不能共存），"
            L"请先在 OpenSpeedy 中 eject 或结束其加速");
        return msg;
    }
    if (IsInjected(pid)) {
        // 确保共享内存存在且已启用
        auto it = states_.find(pid);
        if (it == states_.end()) {
            SharedState st;
            if (st.CreateOrOpen(pid)) {
                BoosterShared v{};
                v.enabled = 1;
                v.groupMask = GROUP_A1 | GROUP_A2 | GROUP_B;
                v.factorA1 = v.factorA2 = 5.0;
                v.factorB = 1.0;
                v.probePeriodMs = PROBE_PERIOD_DEFAULT;
                st.Write(v);
                states_.emplace(pid, std::move(st));
            }
        } else {
            BoosterShared cur{};
            it->second.Read(cur);
            cur.enabled = 1;
            it->second.Write(cur);
        }
        StringCchPrintfW(msg, 256, L"已注入过，直接启用（pid %lu）", pid);
        return msg;
    }

    std::wstring dll = DllPathFor(pid);
    if (GetFileAttributesW(dll.c_str()) == INVALID_FILE_ATTRIBUTES) {
        StringCchPrintfW(msg, 256, L"找不到 hook DLL：%s", dll.c_str());
        return msg;
    }

    // 共享内存先行（DLL 加载时即可读到 enabled/mask/factors）
    SharedState& st = states_[pid];
    if (!st.CreateOrOpen(pid)) {
        StringCchPrintfW(msg, 256, L"创建共享内存失败（pid %lu）", pid);
        return msg;
    }
    BoosterShared v{};
    v.enabled = 1;
    v.groupMask = GROUP_A1 | GROUP_A2 | GROUP_B;
    v.factorA1 = v.factorA2 = 5.0;
    v.factorB = 1.0;
    v.probePeriodMs = PROBE_PERIOD_DEFAULT;
    st.Write(v);

    // 位数匹配注入（x86 目标必须走 32 位助手；这是百度客户端的常态）
    unsigned long helperExit = 0;
    std::wstring helperErr = InjectViaHelper(pid, dll, &helperExit);
    if (!helperErr.empty()) {
        if (helperErr.rfind(L"MISSING:", 0) == 0) {
            StringCchPrintfW(msg, 256,
                L"缺少位数匹配的注入助手：%s（请运行 native\\build.bat）",
                helperErr.c_str() + 8);
            return msg;
        }
        // 助手退出码 → 失败环节
        static const wchar_t* kStage[] = {
            L"成功", L"OpenProcess 被拒（需管理员）", L"VirtualAllocEx 失败",
            L"WriteProcessMemory 失败", L"GetProcAddress 失败",
            L"CreateRemoteThread 失败（可能被杀软拦截）",
            L"LoadLibrary 返回 NULL（DLL 被目标拒绝）",
        };
        const wchar_t* stage = helperExit < 7 ? kStage[helperExit] : L"未知失败";
        StringCchPrintfW(msg, 256, L"注入失败：%s（助手退出码 %lu，%ls）",
                         stage, helperExit,
                         IsWow64(pid) ? L"32 位目标 → inject32.exe"
                                      : L"64 位目标 → inject64.exe");
        return msg;
    }

    if (!IsInjected(pid)) {
        // 助手成功但模块列表里没有：DllMain 可能直接返回 FALSE（hook 装不上）
        SharedState probe;
        BoosterShared v2{};
        if (probe.CreateOrOpen(pid) && probe.Read(v2)) {
            if (v2.sentinel == 0x424F4F53ULL) {
                StringCchPrintfW(msg, 256,
                    L"DLL 已加载但 hook 未生效（status=%llu detail=%llu）——"
                    L"可能被安全软件拦截", v2.status, v2.detail);
                return msg;
            }
        }
        StringCchPrintfW(msg, 256,
            L"注入后未在模块列表发现 hook DLL（DllMain 可能返回了 FALSE）");
        return msg;
    }
    StringCchPrintfW(msg, 256, L"注入成功（pid %lu，%s）", pid, dll.c_str());
    return msg;
}

std::wstring Injector::Eject(unsigned long pid) {
    auto it = states_.find(pid);
    if (it == states_.end()) {
        // 新控制器实例接管旧会话：打开既有映射写卸载标志
        SharedState st;
        if (!st.CreateOrOpen(pid))
            return L"该进程未注入（本控制器也没有其共享内存）";
        BoosterShared v{};
        st.Read(v);
        v.uninstall = 1;
        st.Write(v);
        wchar_t msg[160]{};
        StringCchPrintfW(msg, 160, L"已向 pid %lu 发送自卸载协议", pid);
        return msg;
    }
    BoosterShared v{};
    it->second.Read(v);
    v.uninstall = 1;
    it->second.Write(v);
    wchar_t msg[160]{};
    StringCchPrintfW(msg, 160, L"已向 pid %lu 发送自卸载协议", pid);
    return msg;
}

bool Injector::SetEnabled(unsigned long pid, bool enabled) {
    auto it = states_.find(pid);
    if (it == states_.end()) return false;
    BoosterShared v{};
    it->second.Read(v);
    v.enabled = enabled ? 1 : 0;
    it->second.Write(v);
    return true;
}

bool Injector::SetFactor(unsigned long pid, double f) {
    auto it = states_.find(pid);
    if (it == states_.end()) return false;
    BoosterShared v{};
    it->second.Read(v);
    v.factorA1 = f;
    v.factorA2 = f;
    v.factorB = 1.0;
    // 速率测量不变量：B 组必须留在 mask 里且因子保持 1.0。
    // 若把 B 从 mask 摘掉，RefreshIfChanged 里 FA2/FB 的门控会连 A2 一起按 1.0 走，
    // 于是"只有 A 被缩放"退化成"什么都没缩放"。正确做法是 B 常驻 mask、因子恒 1.0。
    v.groupMask = GROUP_A1 | GROUP_A2 | GROUP_B;
    v.factorB = RATE_SAFE_FACTOR_B;
    if (v.probePeriodMs == 0) v.probePeriodMs = PROBE_PERIOD_DEFAULT;
    it->second.Write(v);
    return true;
}

bool Injector::GetStatus(unsigned long pid, BoosterShared& out) const {
    auto it = states_.find(pid);
    if (it == states_.end()) return false;
    return it->second.Read(out);
}

std::vector<unsigned long> Injector::InjectedPids() const {
    std::vector<unsigned long> out;
    for (auto& kv : states_) out.push_back(kv.first);
    return out;
}

std::vector<unsigned long> Injector::WatchTick() {
    std::vector<unsigned long> added;
    // 1) 清掉已退出的进程：客户端会自己重启 baidunetdiskhost.exe，
    //    旧 pid 的映射留着只会让 InjectedPids() 越滚越大、SetFactor 打空。
    for (auto it = states_.begin(); it != states_.end(); ) {
        HANDLE h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, it->first);
        if (!h) { it = states_.erase(it); continue; }
        CloseHandle(h);
        ++it;
    }
    // 2) 只注入"真下载引擎"（带 kernel.dll 的 host），不按进程名盲注全部白名单：
    //    盲注会把 CEF 渲染进程和 vastplayer 宿主一起 hook，把 UI 时钟也缩放。
    //    kernel.dll 未加载（客户端刚起/没有下载任务）时**什么都不做**，
    //    而不是随便挑一个同名进程——那只会得到"注入成功但倍率无效"的假象。
    for (const Target& t : FindBaiduTargets()) {
        if (_wcsicmp(t.name.c_str(), kEngineProcess) != 0) continue;
        if (!IsEngine(t.pid)) continue;
        if (states_.find(t.pid) != states_.end()) continue;
        std::wstring r = Inject(t.pid);
        if (IsInjected(t.pid)) added.push_back(t.pid);
        (void)r;
    }
    return added;
}

} // namespace booster
