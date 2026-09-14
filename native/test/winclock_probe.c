// winclock_probe.c -- 时钟探针（可编 x86/x64）
//
// 用途：作为 hook 注入的靶子。它自己周期性测量"被 hook 的时钟"相对
// "真实墙钟"的比值，把结果写进 argv[1] 指定的文件。这样验证完全不依赖
// 控制器侧的推测：文件里出现 5.0 就是 hook 真的生效了，1.0 就是没生效。
//
// 真实墙钟用 GetSystemTime（不在 hook 名单内，MinHook 改不到它）；
// 虚拟时钟用 QueryPerformanceCounter（在 GROUP_A1 名单内）。
//
// 构建：cl /nologo /MT /O2 /utf-8 winclock_probe.c /Fe:winclock32.exe (x86)
//      cl /nologo /MT /O2 /utf-8 winclock_probe.c /Fe:winclock64.exe (x64)
// 用法：winclock32.exe out.txt [总时长秒]
#include <windows.h>
#include <stdio.h>

static double wallMs(const SYSTEMTIME* s) {
    return ((double)s->wHour * 3600.0 + s->wMinute * 60.0 + s->wSecond) * 1000.0
           + s->wMilliseconds;
}

int main(int argc, char** argv) {
    const char* outPath = argc > 1 ? argv[1] : "winclock_probe.txt";
    int totalSec = argc > 2 ? atoi(argv[2]) : 30;

    LARGE_INTEGER freq;
    QueryPerformanceFrequency(&freq);

    FILE* f = fopen(outPath, "w");
    if (!f) return 1;
    fprintf(f, "# pid=%lu bits=%d freq=%lld\n",
            (unsigned long)GetCurrentProcessId(), (int)(sizeof(void*) * 8),
            (long long)freq.QuadPart);
    fflush(f);

    for (int i = 0; i < totalSec; i++) {
        SYSTEMTIME s0, s1;
        LARGE_INTEGER q0, q1;
        GetSystemTime(&s0);
        QueryPerformanceCounter(&q0);
        Sleep(1000);
        QueryPerformanceCounter(&q1);
        GetSystemTime(&s1);

        double realMs = wallMs(&s1) - wallMs(&s0);
        if (realMs < 0) realMs += 86400000.0;
        double virtMs = (double)(q1.QuadPart - q0.QuadPart) * 1000.0
                        / (double)freq.QuadPart;
        double ratio = realMs > 1.0 ? virtMs / realMs : 0.0;
        fprintf(f, "t=%d realMs=%.1f virtMs=%.1f ratio=%.3f\n",
                i, realMs, virtMs, ratio);
        fflush(f);
    }
    fclose(f);
    return 0;
}
