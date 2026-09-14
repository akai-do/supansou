// Booster Hook DLL 入口
#include <windows.h>
#include "hook_internal.h"

static HANDLE g_monitorThread = nullptr;

BOOL APIENTRY DllMain(HMODULE hModule, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(hModule);
        // 阶段 1：kernel32 hook（loader-lock 安全；任一失败 → DLL 加载失败，
        // 进程不受影响地保持无 hook 状态）
        if (!booster::InitBoosterDllStage1(hModule))
            return FALSE;
        // 阶段 2 线程：winmm hook + 共享内存监视 + 自卸载
        g_monitorThread = CreateThread(nullptr, 0,
            booster::MonitorThreadExported, nullptr, 0, nullptr);
        if (g_monitorThread) CloseHandle(g_monitorThread);
    }
    return TRUE;
}
