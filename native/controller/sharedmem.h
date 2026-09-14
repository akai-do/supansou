// 控制器侧共享内存：创建/写入/读取（seqlock 写侧 + 状态读取）
#pragma once
#include <windows.h>
#include "../hook/booster_shared.h"

namespace booster {

class SharedState {
public:
    bool CreateOrOpen(unsigned long pid);      // 注入前创建；已存在则打开
    void Write(const BoosterShared& v);        // seqlock 写
    bool Read(BoosterShared& out) const;       // 读当前状态
    void Close();                              // 释放句柄（DLL 视图仍在则映射存活）
    bool Valid() const { return view_ != nullptr; }
    unsigned long Pid() const { return pid_; }

private:
    unsigned long pid_ = 0;
    HANDLE map_ = nullptr;
    BoosterShared* view_ = nullptr;
    unsigned long revision_ = 0;
};

} // namespace booster
