// Hook 内部声明：真实函数指针、安装与刷新接口
#pragma once
#include <windows.h>

namespace booster {

// ---------------- 真实函数指针类型 ----------------
typedef BOOL(WINAPI* PQPC_t)(LARGE_INTEGER*);
typedef DWORD(WINAPI* PGetTickCount_t)(void);
typedef ULONGLONG(WINAPI* PGetTickCount64_t)(void);
typedef void(WINAPI* PGetFT_t)(LPFILETIME);
typedef DWORD(WINAPI* PtimeGetTime_t)(void);
typedef void(WINAPI* PSleep_t)(DWORD);
typedef DWORD(WINAPI* PSleepEx_t)(DWORD, BOOL);
typedef DWORD(WINAPI* PW4SO_t)(HANDLE, DWORD);
typedef DWORD(WINAPI* PW4SOEx_t)(HANDLE, DWORD, BOOL);
typedef DWORD(WINAPI* PW4MO_t)(DWORD, const HANDLE*, BOOL, DWORD);
typedef DWORD(WINAPI* PW4MOEx_t)(DWORD, const HANDLE*, BOOL, DWORD, BOOL);

extern PQPC_t            Real_QPC;
extern PGetTickCount_t   Real_GetTickCount;
extern PGetTickCount64_t Real_GetTickCount64;
extern PGetFT_t          Real_GetSystemTimeAsFileTime;
extern PGetFT_t          Real_GetSystemTimePreciseAsFileTime;
extern PtimeGetTime_t    Real_timeGetTime;
extern PSleep_t          Real_Sleep;
extern PSleepEx_t        Real_SleepEx;
extern PW4SO_t           Real_WaitForSingleObject;
extern PW4SOEx_t         Real_WaitForSingleObjectEx;
extern PW4MO_t           Real_WaitForMultipleObjects;
extern PW4MOEx_t         Real_WaitForMultipleObjectsEx;

// DllMain 阶段：安装 kernel32 hook（loader-lock 安全，不 LoadLibrary）
bool InstallKernelHooks();
// MonitorThread 阶段：安装 winmm timeGetTime hook（loader-lock 外）
bool InstallWinmmHook();
// 共享 revision 变化时：重载生效参数并重锚时钟族
void RefreshIfChanged();
// 监视线程主体（阶段 2 + 自卸载监视）
DWORD WINAPI MonitorThreadExported(LPVOID);
// DllMain 阶段 1 总入口（含共享内存视图初始化）
bool InitBoosterDllStage1(HMODULE hSelf);
bool ShouldUninstall();
bool HooksActive();

} // namespace booster
