# -*- coding: utf-8 -*-
"""读取百度客户端 transmission.db 的下载任务队列（只读副本，不动原库）。

用法：
  python scripts\\_diag_client_tasks.py list            # 任务概览 + 状态统计
  python scripts\\_diag_client_tasks.py show <task_id>  # 单任务全字段（不截断）

写库不在这里做：改任务状态必须「关客户端 → 改 → 开客户端」，
见 _diag_client_tasks.py 的说明与 boost 子命令。
"""
import os
import shutil
import sqlite3
import sys
import tempfile

DB_DIR = (r"D:\A2000-soft\百度网盘\BaiduNetdisk\module\BrowserEngine"
          r"\users\0e290f269136aca220af8e42ae0b907d")
DB = os.path.join(DB_DIR, "transmission.db")

FIELDS = ("task_id", "server_path", "local_path", "status", "file_size",
          "complete_size", "isdir", "error_code", "add_time",
          "status_changetime", "download_url", "cmd_type", "trans_id",
          "md5", "context")


def snapshot():
    """把 db + wal + shm 复制到临时目录后打开（避免读脏页，也绝不锁原库）。"""
    tmp = tempfile.mkdtemp(prefix="bdtrans_")
    base = os.path.join(tmp, "transmission.db")
    for suf in ("", "-wal", "-shm"):
        src = DB + suf
        if os.path.exists(src):
            shutil.copy2(src, base + suf)
    return sqlite3.connect(base), base


def fmt_size(n):
    if n is None:
        return "-"
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}PB"


def cmd_list(_a):
    conn, _ = snapshot()
    rows = list(conn.execute(
        f"select {','.join(FIELDS)} from download_file order by status_changetime desc"))
    print(f"download_file 共 {len(rows)} 行\n")
    from collections import Counter
    c = Counter(r[3] for r in rows)
    print("status 分布:", dict(sorted(c.items())))
    print("cmd_type 分布:", dict(Counter(r[11] for r in rows)))
    print()
    print(f"{'task_id':<12}{'status':>7}{'size':>11}{'done':>11}{'%':>7}"
          f"{'isdir':>6}  local_path")
    for r in rows[:60]:
        d = dict(zip(FIELDS, r))
        pct = (d["complete_size"] / d["file_size"] * 100) if d["file_size"] else 0
        lp = (d["local_path"] or "")[-70:]
        print(f"{d['task_id']:<12}{d['status']:>7}{fmt_size(d['file_size']):>11}"
              f"{fmt_size(d['complete_size']):>11}{pct:>6.0f}%{d['isdir']:>6}  {lp}")
    if len(rows) > 60:
        print(f"...（另有 {len(rows)-60} 行）")
    conn.close()
    return 0


def cmd_show(a):
    conn, _ = snapshot()
    r = conn.execute(f"select {','.join(FIELDS)} from download_file where task_id=?",
                     (int(a.task_id),)).fetchone()
    if not r:
        print("没有该 task_id")
        return 1
    for k, v in zip(FIELDS, r):
        print(f"{k:<20}= {v}")
    conn.close()
    return 0


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] == "list":
        return cmd_list(None)
    if argv[0] == "show":
        class A:
            task_id = argv[1]
        return cmd_show(A())
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
