# -*- coding: utf-8 -*-
"""百度客户端「恢复下载」驱动器：用任务库（transmission.db）状态字段让客户端重新开始下载。

为什么走这条路：客户端 UI 是 CEF 渲染但没有可用的无障碍树（UIA 只暴露一个
'Chrome Legacy Window'），没有本地 HTTP/CLI 接口，窗口又是 50x162 的收缩胶囊，
所以「点一下继续下载」无法自动化。任务库是唯一可靠的可编程入口。

安全约定：
  - 改库前**必须**先退客户端，并整库备份（含 -wal/-shm）到 backend/data/；
  - 只改 download_file.status，不碰 url/凭证/其他表；
  - 提供 restore 子命令一键回滚。

用法：
  python scripts\\_diag_client_resume.py status            # 看当前队列（不关客户端）
  python scripts\\_diag_client_resume.py probe            # 暴力识别「下载中」的 status 值
  python scripts\\_diag_client_resume.py set --status 1   # 关客户端→改库→开客户端
  python scripts\\_diag_client_resume.py restore          # 关客户端→回滚库备份→开客户端
"""
import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
from accel import openspeedy as osd  # noqa: E402

CLIENT_DIR = r"D:\A2000-soft\百度网盘\BaiduNetdisk"
CLIENT = os.path.join(CLIENT_DIR, "BaiduNetdisk.exe")
DB_DIR = os.path.join(CLIENT_DIR,
                      r"module\BrowserEngine\users\0e290f269136aca220af8e42ae0b907d")
DB = os.path.join(DB_DIR, "transmission.db")
BACKUP = os.path.join(ROOT, "backend", "data", "task_db_backup")

KILL_LIST = ["BaiduNetdisk.exe", "BaiduNetdiskUnite.exe", "baidunetdiskhost.exe",
             "YunDetectService.exe"]


# ---------------------------------------------------------------- client ctl
def kill_client(silent=False):
    for name in KILL_LIST:
        r = subprocess.run(["taskkill", "/IM", name, "/F", "/T"],
                           capture_output=True, encoding="mbcs", errors="replace")
        if not silent:
            ok = "成功" in (r.stdout or "") or "SUCCESS" in (r.stdout or "").upper()
            print(f"  kill {name:<24} {'ok' if ok else '（未在运行）'}")
    for _ in range(20):
        if not osd.list_baidu_processes():
            return True
        time.sleep(0.5)
    return not osd.list_baidu_processes()


def start_client(wait=25):
    print(f"  start {CLIENT}")
    subprocess.Popen([CLIENT], cwd=CLIENT_DIR)
    t0 = time.time()
    while time.time() - t0 < wait:
        time.sleep(1)
        if any(p["name"].lower() == "baidunetdisk.exe"
               for p in osd.list_baidu_processes()):
            time.sleep(6)          # 等引擎把任务容器拉起来
            return True
    return False


def backup_db():
    os.makedirs(BACKUP, exist_ok=True)
    for suf in ("", "-wal", "-shm"):
        src = DB + suf
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(BACKUP, os.path.basename(src)))
    print(f"  备份 → {BACKUP}")


def restore_db():
    for suf in ("", "-wal", "-shm"):
        src = os.path.join(BACKUP, os.path.basename(DB) + suf)
        dst = DB + suf
        if os.path.exists(src):
            shutil.copy2(src, dst)
        elif os.path.exists(dst):
            os.remove(dst)
    print(f"  已从 {BACKUP} 回滚任务库")


def edit_status(status=None, task_ids=None, only_incomplete=True):
    """把 download_file 的 status 改成目标值；only_incomplete 只动「未下完」的。

    注意 done 可以为 0（全新任务），所以判据必须是 `(done or 0) < size`，
    不能写成 `done and done < size`——后者会把**所有 0 进度的任务静默跳过**，
    于是"武装一个全新的大文件"永远改不动任何行（实测踩到：改了 0 个任务）。
    isdir=1 的目录任务仍然跳过（客户端对目录任务的调度与单文件不同）。
    """
    conn = sqlite3.connect(DB)
    try:
        # 先把 WAL 合并进主库，避免改动后旧 WAL 覆盖
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        rows = list(conn.execute(
            "select task_id,status,file_size,complete_size,isdir from download_file"))
        changed = []
        for tid, st, size, done, isdir in rows:
            if task_ids and tid not in task_ids:
                continue
            if isdir:
                continue
            if only_incomplete and not (size and (done or 0) < size):
                continue
            conn.execute("update download_file set status=? where task_id=?",
                         (status, tid))
            changed.append(tid)
        conn.commit()
        print(f"  改了 {len(changed)} 个任务 status={status}: {changed[:12]}"
              f"{' …' if len(changed) > 12 else ''}")
        return changed
    finally:
        conn.close()


def sample_rate(seconds=15):
    """采样客户端总网络读速率，返回每秒 bps 列表。"""
    s = osd.SpeedSampler()
    pids = [p["pid"] for p in osd.list_baidu_processes()]
    if not pids:
        return []
    s.sample(pids)
    out = []
    t0 = time.time()
    while time.time() - t0 < seconds:
        time.sleep(1.0)
        pids = [p["pid"] for p in osd.list_baidu_processes()]
        out.append(s.sample(pids))
    return out


def fmt(bps):
    return f"{bps/1e6:.2f}MB/s" if bps >= 1e6 else f"{bps/1e3:.0f}KB/s"


# ---------------------------------------------------------------- commands
def cmd_status(_a):
    r = subprocess.run([sys.executable,
                        os.path.join(ROOT, "scripts", "_diag_client_tasks.py"),
                        "list"], encoding="utf-8", errors="replace",
                       capture_output=True)
    print(r.stdout or r.stderr)
    return 0


def cmd_probe(a):
    """暴力识别：哪个 status 值会让客户端真正开始下载。"""
    if not os.path.exists(DB):
        print(f"!! 找不到任务库 {DB}")
        return 1
    killed = kill_client()
    print(f"  客户端已关闭: {killed}")
    if not killed:
        return 1
    backup_db()

    results = {}
    try:
        for cand in a.candidates:
            print(f"\n===== 候选 status={cand} =====")
            restore_db()
            edit_status(status=cand)
            if not start_client():
                print("  !! 客户端没起来")
                results[cand] = None
                kill_client(silent=True)
                continue
            rates = sample_rate(a.seconds)
            peak = max(rates) if rates else 0.0
            avg = sum(rates) / len(rates) if rates else 0.0
            results[cand] = (avg, peak)
            print(f"  status={cand}: 均值 {fmt(avg)} 峰值 {fmt(peak)} "
                  f"→ {'下载中 ✔' if avg > 100*1024 else '未下载'}")
            kill_client(silent=True)
    finally:
        # 不管结果如何都回到原始库
        kill_client(silent=True)
        restore_db()
        start_client()
    print("\n===== 探测结果 =====")
    for cand, res in results.items():
        print(f"  status={cand}: " + ("未测到" if res is None
                                     else f"均值 {fmt(res[0])} 峰值 {fmt(res[1])}"))
    return 0


def cmd_set(a):
    killed = kill_client()
    print(f"  客户端已关闭: {killed}")
    if not killed:
        return 1
    backup_db()
    ids = [int(x) for x in a.task_ids.split(",")] if a.task_ids else None
    edit_status(status=a.status, task_ids=ids,
                only_incomplete=not a.all_tasks)
    ok = start_client()
    print(f"  客户端已启动: {ok}")
    return 0 if ok else 1


def cmd_restore(_a):
    killed = kill_client()
    print(f"  客户端已关闭: {killed}")
    if not killed:
        return 1
    restore_db()
    ok = start_client()
    print(f"  客户端已启动: {ok}")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="百度客户端恢复下载驱动器")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("status", help="查看任务队列")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("probe", help="暴力识别「下载中」status 值")
    p.add_argument("--candidates", default="1,2,4,5,6,7,8,9,0",
                   help="逗号分隔的候选值")
    p.add_argument("--seconds", type=int, default=15)
    p.set_defaults(func=lambda a: cmd_probe(
        type("A", (), {"candidates": [int(x) for x in a.candidates.split(",")],
                       "seconds": a.seconds})))

    p = sub.add_parser("set", help="关客户端→改 status→开客户端")
    p.add_argument("--status", type=int, required=True)
    p.add_argument("--task-ids", default="")
    p.add_argument("--all-tasks", action="store_true",
                   help="连已完成/无进度的任务一起改")
    p.set_defaults(func=cmd_set)

    p = sub.add_parser("restore", help="回滚任务库并重启客户端")
    p.set_defaults(func=cmd_restore)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
