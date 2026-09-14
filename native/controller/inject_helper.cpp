// inject_helper.cpp -- bitness-matched remote LoadLibrary injector.
//
// Why this exists: CreateRemoteThread needs the start address to be valid in the
// TARGET address space. A 64-bit booster.exe resolving LoadLibraryW from its own
// kernel32 gives a 64-bit address, which is meaningless inside a WOW64 (32-bit)
// target -- the injected thread just returns 0 and nothing loads. The Baidu
// client is WOW64, so x86 targets are the common case, not the edge case.
//
// So booster.exe (x64) shells out to inject32.exe for 32-bit targets and to
// inject64.exe for native 64-bit targets. Compiled twice from this one file.
//
// Usage: inject_helper.exe <pid> <dll-path>
// Exit codes: 0 ok / 1 OpenProcess / 2 VirtualAllocEx / 3 WriteProcessMemory
//             / 4 GetProcAddress / 5 CreateRemoteThread / 6 LoadLibrary returned NULL
#include <windows.h>
#include <stdio.h>

int main(int argc, char** argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: %s <pid> <dll-path>\n", argv[0]);
        return 100;
    }
    DWORD pid = (DWORD)atoi(argv[1]);
    const char* dllPath = argv[2];
    int dllLen = (int)strlen(dllPath) + 1;

    HANDLE h = OpenProcess(PROCESS_CREATE_THREAD | PROCESS_QUERY_INFORMATION |
                               PROCESS_VM_OPERATION | PROCESS_VM_WRITE |
                               PROCESS_VM_READ,
                           FALSE, pid);
    if (!h) return 1;

    void* remote = VirtualAllocEx(h, NULL, (SIZE_T)dllLen,
                                  MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!remote) {
        CloseHandle(h);
        return 2;
    }
    if (!WriteProcessMemory(h, remote, dllPath, (SIZE_T)dllLen, NULL)) {
        VirtualFreeEx(h, remote, 0, MEM_RELEASE);
        CloseHandle(h);
        return 3;
    }

    HMODULE k32 = GetModuleHandleW(L"kernel32.dll");
    LPTHREAD_START_ROUTINE loadlib =
        (LPTHREAD_START_ROUTINE)GetProcAddress(k32, "LoadLibraryA");
    if (!loadlib) {
        VirtualFreeEx(h, remote, 0, MEM_RELEASE);
        CloseHandle(h);
        return 4;
    }

    HANDLE th = CreateRemoteThread(h, NULL, 0, loadlib, remote, 0, NULL);
    if (!th) {
        VirtualFreeEx(h, remote, 0, MEM_RELEASE);
        CloseHandle(h);
        return 5;
    }

    WaitForSingleObject(th, 10000);
    DWORD moduleBase = 0;
    GetExitCodeThread(th, &moduleBase);
    CloseHandle(th);
    VirtualFreeEx(h, remote, 0, MEM_RELEASE);
    CloseHandle(h);

    // LoadLibrary returns the module handle; 0/NULL means it refused to load.
    return moduleBase == 0 ? 6 : 0;
}
