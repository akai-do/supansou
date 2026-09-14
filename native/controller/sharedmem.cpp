#include "sharedmem.h"
#include <strsafe.h>

namespace booster {

bool SharedState::CreateOrOpen(unsigned long pid) {
    if (view_ && pid_ == pid) return true;
    Close();
    wchar_t name[64];
    MapNameForPid(pid, name, 64);
    // 先尝试打开（DLL 可能先创建过？——DLL 只读不创建；控制器创建）
    map_ = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr,
                              PAGE_READWRITE, 0, sizeof(BoosterShared), name);
    if (!map_) return false;
    bool existed = GetLastError() == ERROR_ALREADY_EXISTS;
    view_ = (BoosterShared*)MapViewOfFile(map_, FILE_MAP_ALL_ACCESS, 0, 0,
                                          sizeof(BoosterShared));
    if (!view_) {
        CloseHandle(map_);
        map_ = nullptr;
        return false;
    }
    pid_ = pid;
    // 关键：接管既有映射时必须续接 revision，否则 DLL 会把新写入
    // 误判为"revision 未变化"而忽略参数更新
    BoosterShared cur;
    if (existed && Read(cur))
        revision_ = cur.revision;
    else
        revision_ = 0;
    return true;
}

void SharedState::Write(const BoosterShared& v) {
    if (!view_) return;
    view_->seq++;                       // odd：写入中
    MemoryBarrier();
    view_->revision = revision_ + 1;
    view_->enabled = v.enabled;
    view_->groupMask = v.groupMask;
    view_->factorA1 = v.factorA1;
    view_->factorA2 = v.factorA2;
    view_->factorB = v.factorB;
    view_->uninstall = v.uninstall;
    // 自测节拍也必须透传：否则每次 setspeed 都会把它清零（0 = 回默认值）
    view_->probePeriodMs = v.probePeriodMs;
    MemoryBarrier();
    view_->seq++;                       // even：稳定（seq 2 步进保持偶偶交替）
    revision_ = view_->revision;
}

bool SharedState::Read(BoosterShared& out) const {
    if (!view_) return false;
    unsigned long s1, s2;
    do {
        s1 = view_->seq;
        if (s1 & 1) return false;
        out = *view_;
        s2 = view_->seq;
    } while (s1 != s2);
    return true;
}

void SharedState::Close() {
    if (view_) { UnmapViewOfFile(view_); view_ = nullptr; }
    if (map_) { CloseHandle(map_); map_ = nullptr; }
    pid_ = 0;
    revision_ = 0;
}

} // namespace booster
