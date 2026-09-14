"""加速模块本地存储：BDUSS 设置 + 下载任务表 + 账号池（stdlib sqlite3, WAL）。

安全约定：
- 数据库固定在 backend/data/accel.db（仓库 .gitignore 已排除 backend/data/）。
- BDUSS 只存 settings/accounts 表；任何日志、接口响应都不输出完整值（仅返回尾 4 位掩码）。
- dlink 直链不落库不缓存（有时效且含签名，取出立即交给 aria2）。

第三版新增列（用 _migrate() 做增量 ALTER，老库可直接升级）：
  priority / group_id / seg_index / seg_total / part_start / part_end /
  part_out / account_id / hidden / phase
"""
import os
import sqlite3
import threading
import time

# accel_tasks 的增量列：列名 → 列定义
_TASK_COLUMNS = {
    "priority":    "INTEGER NOT NULL DEFAULT 0",
    "group_id":    "TEXT NOT NULL DEFAULT ''",
    "seg_index":   "INTEGER NOT NULL DEFAULT -1",
    "seg_total":   "INTEGER NOT NULL DEFAULT 0",
    "part_start":  "INTEGER NOT NULL DEFAULT 0",
    "part_end":    "INTEGER NOT NULL DEFAULT 0",
    "part_out":    "TEXT NOT NULL DEFAULT ''",
    "account_id":  "INTEGER NOT NULL DEFAULT 0",
    "hidden":      "INTEGER NOT NULL DEFAULT 0",
    "phase":       "TEXT NOT NULL DEFAULT 'download'",
}


def mask_secret(value: str) -> str:
    """凭证掩码：只留尾 4 位，供前端展示。"""
    if not value:
        return ""
    return "****" + value[-4:] if len(value) > 8 else "****"


class AccelStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._local = threading.local()
        self._write_lock = threading.Lock()
        self._init_schema()
        self._migrate()

    @property
    def _conn(self) -> sqlite3.Connection:
        if getattr(self._local, "conn", None) is None:
            conn = sqlite3.connect(self.db_path, timeout=15)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")
            self._local.conn = conn
        return self._local.conn

    def _init_schema(self):
        with self._write_lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS accel_settings (
                    key        TEXT PRIMARY KEY,
                    value      TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS accel_tasks (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    name        TEXT NOT NULL,
                    size        INTEGER NOT NULL DEFAULT 0,
                    save_dir    TEXT NOT NULL DEFAULT '',
                    fid         TEXT NOT NULL DEFAULT '',
                    file_path   TEXT NOT NULL DEFAULT '',
                    surl        TEXT NOT NULL DEFAULT '',
                    pwd         TEXT NOT NULL DEFAULT '',
                    shareid     TEXT NOT NULL DEFAULT '',
                    uk          TEXT NOT NULL DEFAULT '',
                    sign        TEXT NOT NULL DEFAULT '',
                    timestamp   TEXT NOT NULL DEFAULT '',
                    aria2_gid   TEXT NOT NULL DEFAULT '',
                    status      TEXT NOT NULL DEFAULT 'downloading',
                    error       TEXT NOT NULL DEFAULT '',
                    created_at  REAL NOT NULL,
                    updated_at  REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS accel_accounts (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    label          TEXT NOT NULL DEFAULT '',
                    bduss          TEXT NOT NULL,
                    stoken         TEXT NOT NULL DEFAULT '',
                    enabled        INTEGER NOT NULL DEFAULT 1,
                    cooldown_until REAL NOT NULL DEFAULT 0,
                    last_used      REAL NOT NULL DEFAULT 0,
                    ok_count       INTEGER NOT NULL DEFAULT 0,
                    fail_count     INTEGER NOT NULL DEFAULT 0,
                    created_at     REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS accel_guest_usage (
                    ip    TEXT NOT NULL,
                    day   TEXT NOT NULL,
                    count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (ip, day)
                );
                -- 解析口令的按日用量：fp 是口令的 sha256 前 12 位（不落明文）。
                -- 用途：一天下来能看出哪一条口令在被大量使用 → 泄露了就只删那一行。
                CREATE TABLE IF NOT EXISTS accel_pass_usage (
                    fp    TEXT NOT NULL,
                    day   TEXT NOT NULL,
                    count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (fp, day)
                );
            """)
            self._conn.commit()

    def _migrate(self):
        """给老库补列（sqlite 不支持 IF NOT EXISTS ADD COLUMN，只能先查）。"""
        with self._write_lock:
            have = {r["name"] for r in
                    self._conn.execute("PRAGMA table_info(accel_tasks)")}
            added = []
            for col, decl in _TASK_COLUMNS.items():
                if col not in have:
                    self._conn.execute(
                        f"ALTER TABLE accel_tasks ADD COLUMN {col} {decl}")
                    added.append(col)
            if added:
                self._conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_accel_tasks_group "
                    "ON accel_tasks(group_id)")
                self._conn.commit()
                import logging
                logging.getLogger("accel").info(
                    "[加速] 任务表已升级，新增列：%s", ", ".join(added))

    # ---------- settings ----------
    def get_setting(self, key: str, default=None):
        row = self._conn.execute(
            "SELECT value FROM accel_settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str):
        with self._write_lock:
            self._conn.execute(
                "INSERT INTO accel_settings(key, value, updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, value, time.time()))
            self._conn.commit()

    def delete_setting(self, key: str):
        with self._write_lock:
            self._conn.execute("DELETE FROM accel_settings WHERE key=?", (key,))
            self._conn.commit()

    def get_json(self, key: str, default=None) -> dict:
        """读一段 JSON 设置（提速配置用）。损坏时回退默认，不影响启动。"""
        import json
        raw = self.get_setting(key)
        if not raw:
            return dict(default or {})
        try:
            v = json.loads(raw)
            return v if isinstance(v, dict) else dict(default or {})
        except ValueError:
            return dict(default or {})

    def set_json(self, key: str, value: dict):
        import json
        self.set_setting(key, json.dumps(value, ensure_ascii=False))

    # ---------- tasks ----------
    def create_task(self, name: str, size: int, save_dir: str, fid: str,
                    file_path: str, ctx: dict, **extra) -> int:
        cols = ["name", "size", "save_dir", "fid", "file_path",
                "surl", "pwd", "shareid", "uk", "sign", "timestamp",
                "aria2_gid", "status", "error", "created_at", "updated_at"]
        vals = [name, size, save_dir, fid, file_path,
                ctx.get("surl", ""), ctx.get("pwd", ""),
                str(ctx.get("shareid", "")), str(ctx.get("uk", "")),
                str(ctx.get("sign", "")), str(ctx.get("timestamp", "")),
                ctx.get("gid", ""), "downloading", "", time.time(), time.time()]
        for k, v in extra.items():
            if k in _TASK_COLUMNS:
                cols.append(k)
                vals.append(v)
        placeholders = ",".join("?" * len(cols))
        with self._write_lock:
            cur = self._conn.execute(
                f"INSERT INTO accel_tasks({','.join(cols)}) VALUES({placeholders})",
                tuple(vals))
            self._conn.commit()
            return cur.lastrowid

    def get_task(self, task_id: int):
        return self._conn.execute(
            "SELECT * FROM accel_tasks WHERE id=?", (task_id,)).fetchone()

    def list_tasks(self, limit: int = 200, include_hidden: bool = True):
        sql = "SELECT * FROM accel_tasks"
        if not include_hidden:
            # 分段分任务不单独展示，由头任务聚合
            sql += " WHERE hidden=0 AND (group_id='' OR seg_index=0)"
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        return self._conn.execute(sql, (limit,)).fetchall()

    def update_task(self, task_id: int, **fields):
        if not fields:
            return
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._write_lock:
            self._conn.execute(
                f"UPDATE accel_tasks SET {cols} WHERE id=?",
                (*fields.values(), task_id))
            self._conn.commit()

    def delete_task(self, task_id: int):
        with self._write_lock:
            self._conn.execute("DELETE FROM accel_tasks WHERE id=?", (task_id,))
            self._conn.commit()

    # ---------- 分段分组 ----------
    def group_tasks(self, group_id: str):
        return self._conn.execute(
            "SELECT * FROM accel_tasks WHERE group_id=? ORDER BY seg_index",
            (group_id,)).fetchall()

    def pending_group_ids(self) -> list:
        """所有还没合并完、且全部段都已结束（done/error）的分组。"""
        rows = self._conn.execute(
            "SELECT group_id,"
            " SUM(CASE WHEN status IN ('done','error') THEN 1 ELSE 0 END) AS fin,"
            " COUNT(*) AS total,"
            " SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS errs"
            " FROM accel_tasks WHERE group_id<>'' AND phase<>'done'"
            " GROUP BY group_id HAVING fin = total").fetchall()
        return [r["group_id"] for r in rows if not r["errs"]]

    # ---------- 账号池 ----------
    def add_account(self, label: str, bduss: str, stoken: str = "") -> int:
        with self._write_lock:
            cur = self._conn.execute(
                "INSERT INTO accel_accounts(label, bduss, stoken, created_at)"
                " VALUES(?,?,?,?)", (label or "", bduss, stoken, time.time()))
            self._conn.commit()
            return cur.lastrowid

    def list_accounts(self):
        return self._conn.execute(
            "SELECT * FROM accel_accounts ORDER BY id").fetchall()

    def get_account(self, account_id: int):
        return self._conn.execute(
            "SELECT * FROM accel_accounts WHERE id=?", (account_id,)).fetchone()

    def update_account(self, account_id: int, **fields):
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._write_lock:
            self._conn.execute(
                f"UPDATE accel_accounts SET {cols} WHERE id=?",
                (*fields.values(), account_id))
            self._conn.commit()

    def delete_account(self, account_id: int):
        with self._write_lock:
            self._conn.execute("DELETE FROM accel_accounts WHERE id=?",
                               (account_id,))
            self._conn.commit()

    def bump_account(self, account_id: int, ok: bool):
        col = "ok_count" if ok else "fail_count"
        with self._write_lock:
            self._conn.execute(
                f"UPDATE accel_accounts SET {col}={col}+1, last_used=? WHERE id=?",
                (time.time(), account_id))
            self._conn.commit()

    def set_account_cooldown(self, account_id: int, until: float):
        self.update_account(account_id, cooldown_until=until)

    # ---------- 访客配额（按 IP 按天） ----------
    def guest_count(self, ip: str, day: str) -> int:
        row = self._conn.execute(
            "SELECT count FROM accel_guest_usage WHERE ip=? AND day=?",
            (ip, day)).fetchone()
        return row["count"] if row else 0

    def guest_incr(self, ip: str, day: str) -> int:
        with self._write_lock:
            self._conn.execute(
                "INSERT INTO accel_guest_usage(ip, day, count) VALUES(?,?,1) "
                "ON CONFLICT(ip, day) DO UPDATE SET count=count+1",
                (ip, day))
            self._conn.commit()
        return self.guest_count(ip, day)

    # ---------- 解析口令用量（按口令按天） ----------
    def pass_count(self, fp: str, day: str) -> int:
        row = self._conn.execute(
            "SELECT count FROM accel_pass_usage WHERE fp=? AND day=?",
            (fp, day)).fetchone()
        return row["count"] if row else 0

    def pass_incr(self, fp: str, day: str) -> int:
        with self._write_lock:
            self._conn.execute(
                "INSERT INTO accel_pass_usage(fp, day, count) VALUES(?,?,1) "
                "ON CONFLICT(fp, day) DO UPDATE SET count=count+1",
                (fp, day))
            self._conn.commit()
        return self.pass_count(fp, day)

    def pass_usage_today(self, day: str) -> dict:
        """今天各口令的用量 {fp: count}，给站长做"谁在猛用"的排查。"""
        return {r["fp"]: r["count"] for r in self._conn.execute(
            "SELECT fp, count FROM accel_pass_usage WHERE day=?", (day,))}
