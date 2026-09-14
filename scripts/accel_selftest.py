"""提速引擎自检：不需要百度账号，不需要真实网盘，用本地限速 HTTP 服务复现
「慢速任务占满并发槽」并验证调度器把槽抢回给快任务。

跑法：
    python scripts/accel_selftest.py
    python scripts/accel_selftest.py --dir D:\\tmp\\accel-test --seconds 40

自检内容：
  1. tuning / store 迁移 / plan_parts 纯逻辑
  2. 启 aria2c（内置二进制）→ 建两个任务：
       slow.bin  单连接被限速到 ~40 KB/s
       fast.bin  不限速
     并发槽只有 1 个（模拟"慢任务占满带宽"的实测场景）
  3. 断言：调度器在 slow 命中 slow_ticks 后把它 pause 让路，fast 拿到槽并跑完
  4. 断言：让路冷却到期后 slow 被放回（不会被饿死）

退出码 0 = 全部通过。
"""
import argparse
import functools
import http.server
import os
import shutil
import socketserver
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "backend"))

from accel import tuning                                  # noqa: E402
from accel.aria2 import Aria2Manager                      # noqa: E402
from accel.scheduler import BandwidthScheduler            # noqa: E402
from accel.segments import plan_parts                     # noqa: E402
from accel.store import AccelStore                        # noqa: E402

SIZE = 12 * 1024 * 1024          # 每个测试文件 12 MB
SLOW_BPS = 80 * 1024             # slow.bin 的**全局**限速（模拟账号级限速）
FAST_BPS = 4 * 1024 * 1024       # fast.bin 的全局限速（足够快，便于采样）
CHUNK = 16 * 1024

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")


# ---------------------------------------------------------------- 限速服务
class TokenBucket:
    """全局限速桶：**所有连接共用一份预算**，这才是百度账号级限速的真实形态。

    如果只做单连接限速（每个连接各自 40KB/s），aria2 开 16 条连接就把总量
    顶上去了，测出来反而不慢——那测不到问题，也测不出调度器有没有用。
    """

    def __init__(self, bps: int):
        self.bps = max(1, int(bps))
        self._lock = threading.Lock()
        self._next_ok = time.time()

    def consume(self, nbytes: int):
        with self._lock:
            now = time.time()
            start = max(self._next_ok, now)
            self._next_ok = start + nbytes / self.bps
            wait = self._next_ok - now
        if wait > 0:
            time.sleep(min(wait, 1.0))


BUCKETS = {"/slow": None, "/fast": None}


class ThrottledHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _serve(self, bucket: TokenBucket):
        total = SIZE
        rng = self.headers.get("Range")
        start = 0
        end = total - 1
        if rng and rng.startswith("bytes="):
            spec = rng.split("=", 1)[1].split(",")[0]
            a, _, b = spec.partition("-")
            start = int(a) if a else 0
            end = int(b) if b else end
            end = min(end, total - 1)
        length = end - start + 1
        self.send_response(206 if rng else 200)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()
        block = bytes(CHUNK)
        sent = 0
        try:
            while sent < length:
                n = min(CHUNK, length - sent)
                bucket.consume(n)
                self.wfile.write(block[:n])
                sent += n
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError,
                OSError):
            pass

    def do_GET(self):
        for prefix, bucket in BUCKETS.items():
            if self.path.startswith(prefix):
                self._serve(bucket)
                return
        self.send_response(404)
        self.end_headers()


class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start_server():
    BUCKETS["/slow"] = TokenBucket(SLOW_BPS)
    BUCKETS["/fast"] = TokenBucket(FAST_BPS)
    srv = ThreadedServer(("127.0.0.1", 0), ThrottledHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


# ---------------------------------------------------------------- 纯逻辑
def test_pure():
    print("\n[1] 纯逻辑")
    p = tuning.profile("turbo")
    check("档位解析", p["split"] == 32 and p["concurrent"] == 3)
    check("未知档位回退", tuning.profile("nope")["split"] ==
          tuning.PROFILES[tuning.DEFAULT_PROFILE]["split"])
    opts = tuning.task_options({"profile": "extreme"})
    check("档位→aria2 参数", opts["split"] == "64" and
          opts["max-connection-per-server"] == "64", str(opts))
    parts = plan_parts(10, 3)
    check("分段划分", parts == [(0, 0, 2), (1, 3, 5), (2, 6, 9)], str(parts))
    parts = plan_parts(7, 4)
    check("分段覆盖完整", parts[0][1] == 0 and parts[-1][2] == 6 and
          all(parts[i][2] + 1 == parts[i + 1][1] for i in range(len(parts) - 1)),
          str(parts))
    g = tuning.global_options({"profile": "turbo", "overall_limit": 0})
    check("全局参数无硬编码 split", "split" not in g and
          g["max-concurrent-downloads"] == "3")


def test_migration(tmpdir):
    print("\n[2] 老库增量迁移")
    db = os.path.join(tmpdir, "mig.db")
    import sqlite3
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE accel_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            size INTEGER NOT NULL DEFAULT 0, save_dir TEXT NOT NULL DEFAULT '',
            fid TEXT NOT NULL DEFAULT '', file_path TEXT NOT NULL DEFAULT '',
            surl TEXT NOT NULL DEFAULT '', pwd TEXT NOT NULL DEFAULT '',
            shareid TEXT NOT NULL DEFAULT '', uk TEXT NOT NULL DEFAULT '',
            sign TEXT NOT NULL DEFAULT '', timestamp TEXT NOT NULL DEFAULT '',
            aria2_gid TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'downloading', error TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL, updated_at REAL NOT NULL);
        INSERT INTO accel_tasks(name, created_at, updated_at) VALUES('old',0,0);
    """)
    con.commit()
    con.close()
    st = AccelStore(db)
    cols = {r["name"] for r in st._conn.execute("PRAGMA table_info(accel_tasks)")}
    need = {"priority", "group_id", "seg_index", "part_start", "part_end",
            "part_out", "hidden", "phase"}
    check("补列完成", need <= cols, ",".join(sorted(need - cols)) or "")
    rows = st.list_tasks()
    check("老数据保留", len(rows) == 1 and rows[0]["name"] == "old")


# ---------------------------------------------------------------- 调度实测
def test_scheduler(tmpdir, seconds, verbose=False):
    print("\n[3] 调度器实测（本地全局限速服务 + 真实 aria2c）")
    srv, base = start_server()
    print(f"  限速服务: {base}  slow={SLOW_BPS // 1024} KB/s（全局） "
          f"fast={FAST_BPS // 1048576} MB/s（全局）  文件={SIZE // 1048576} MB")

    cfg = tuning.resolve_config({
        "profile": "balanced",
        # 只有一个并发槽 —— 正是"慢任务占满带宽"的复现场景
        "max_concurrent": 1,
        "slow_threshold": 400 * 1024,
        "slow_ticks": 3,
        "yield_enabled": True,
        "yield_cooldown": 12,
        "ramp_enabled": False,
        "overall_limit": 0,
    })

    store = AccelStore(os.path.join(tmpdir, "sched.db"))
    dl = os.path.join(tmpdir, "dl")
    os.makedirs(dl, exist_ok=True)
    manager = Aria2Manager(dl, cfg)
    manager.ensure_running()

    sched = BandwidthScheduler(store, manager, lambda: dict(cfg))
    sched.start()

    slow_gid = manager.add_download(f"{base}/slow.bin", "slow.bin", dl, "", base)
    slow_tid = store.create_task("slow.bin", SIZE, dl, "1", "", {"gid": slow_gid})
    fast_gid = manager.add_download(f"{base}/fast.bin", "fast.bin", dl, "", base)
    fast_tid = store.create_task("fast.bin", SIZE, dl, "2", "", {"gid": fast_gid})
    print(f"  已推送 slow(gid={slow_gid[:8]}) 然后 fast(gid={fast_gid[:8]})，并发槽=1")

    yielded_at, fast_done_at, resumed_at = None, None, None
    slow_seen_speed = fast_seen_speed = 0
    ever_deferred = False
    t0 = time.time()
    while time.time() - t0 < seconds:
        time.sleep(1)
        try:
            ss = manager.status(slow_gid)
            fs = manager.status(fast_gid)
        except Exception:
            break
        el = time.time() - t0
        if ss["status"] == "active":
            slow_seen_speed = max(slow_seen_speed, ss["speed"])
        if fs["status"] == "active":
            fast_seen_speed = max(fast_seen_speed, fs["speed"])
        if verbose:
            print(f"    t={el:5.1f}s slow={ss['status']:<8}{ss['speed'] / 1024:7.0f} KB/s"
                  f" | fast={fs['status']:<8}{fs['speed'] / 1024:7.0f} KB/s"
                  f" | deferred={slow_tid in sched._deferred}")
        if yielded_at is None and slow_tid in sched._deferred:
            yielded_at = el
            ever_deferred = True
            print(f"  t={el:5.1f}s  慢任务被让路 → "
                  f"fast 状态={fs['status']}  速度={fs['speed'] / 1024:.0f} KB/s")
        if fast_done_at is None and fs["status"] == "complete":
            fast_done_at = el
            print(f"  t={el:5.1f}s  快任务完成（峰值 {fast_seen_speed / 1e6:.2f} MB/s）")
        # "放回"必须是"先被让路、之后又被恢复"，否则慢任务从头到尾在跑也会误判
        if fast_done_at is not None and resumed_at is None and ever_deferred and \
                slow_tid not in sched._deferred and ss["status"] == "active":
            resumed_at = el
            print(f"  t={el:5.1f}s  慢任务被放回（冷却到期 + 槽位空出）")
        if fast_done_at is not None and resumed_at is not None:
            break

    view = sched.view()
    print(f"  调度器：tick={view['ticks']} 判定={view['verdict']} "
          f"({view['verdict_text']}) 峰值={view['peak_speed'] / 1e6:.2f} MB/s")
    for line in list(view["log"])[:6]:
        print(f"    日志 {time.strftime('%H:%M:%S', time.localtime(line['ts']))} "
              f"{line['msg']}")

    check("慢任务被调度器让路", yielded_at is not None,
          f"t={yielded_at:.1f}s" if yielded_at else "从未让路")
    check("快任务抢到并发槽并完成", fast_done_at is not None,
          f"t={fast_done_at:.1f}s" if fast_done_at else "未完成")
    check("快任务速度远高于慢任务",
          fast_seen_speed > slow_seen_speed * 3,
          f"fast={fast_seen_speed / 1024:.0f} KB/s vs slow={slow_seen_speed / 1024:.0f} KB/s")
    check("让路任务未被饿死（冷却后被放回）", resumed_at is not None,
          f"t={resumed_at:.1f}s" if resumed_at else "未放回")
    check("归因给出结论", view["verdict"] in
          ("slot_starvation", "per_conn_throttle", "account_cap", "local_limit", "ok"),
          view["verdict_text"])

    sched.stop()
    srv.shutdown()
    return manager


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="")
    ap.add_argument("--seconds", type=int, default=75)
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    tmpdir = args.dir or tempfile.mkdtemp(prefix="accel-selftest-")
    os.makedirs(tmpdir, exist_ok=True)
    print(f"工作目录: {tmpdir}")

    test_pure()
    test_migration(tmpdir)
    mgr = None
    try:
        mgr = test_scheduler(tmpdir, args.seconds, args.verbose)
    finally:
        if mgr:
            mgr.shutdown()
        if not args.keep and not args.dir:
            shutil.rmtree(tmpdir, ignore_errors=True)

    print(f"\n结果：{len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败项：" + "、".join(FAIL))
        return 1
    print("提速引擎自检全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
