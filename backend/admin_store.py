"""管理后台账号与审计日志数据层。

只新增 `admin_users` / `admin_audit_log` 两张表。
后台密码与 ACCEL_TOKEN 完全分离，密码只保存哈希。
"""
import sqlite3
from datetime import datetime

from werkzeug.security import check_password_hash, generate_password_hash

from config import Config


def _connect():
    conn = sqlite3.connect(Config.INDEX_DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def ensure_schema():
    conn = _connect()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS admin_users (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                username             TEXT UNIQUE NOT NULL,
                password_hash        TEXT NOT NULL,
                role                 TEXT DEFAULT 'admin',
                must_change_password INTEGER DEFAULT 1,
                created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login_at        TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS admin_audit_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                username   TEXT DEFAULT '',
                action     TEXT NOT NULL,
                target     TEXT DEFAULT '',
                detail     TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_admin_audit_created
                ON admin_audit_log(created_at);
        """)
        conn.commit()
    finally:
        conn.close()


def ensure_default_admin(username: str, password: str) -> bool:
    """没有管理员且提供了 ADMIN_PASSWORD 时创建默认账号。

    返回是否新建。密码不写日志、不返回给前端。
    """
    username = (username or "").strip()
    if not username or not password:
        return False
    conn = _connect()
    try:
        count = conn.execute("SELECT COUNT(*) FROM admin_users").fetchone()[0]
        if count:
            return False
        password_hash = generate_password_hash(password, method="pbkdf2:sha256")
        conn.execute(
            "INSERT INTO admin_users (username, password_hash, role, "
            "must_change_password, created_at) VALUES (?, ?, 'admin', 1, ?)",
            (username, password_hash, datetime.now().isoformat()),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def count_users() -> int:
    conn = _connect()
    try:
        return int(conn.execute("SELECT COUNT(*) FROM admin_users").fetchone()[0])
    finally:
        conn.close()


def get_user(username: str):
    username = (username or "").strip()
    if not username:
        return None
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id, username, password_hash, role, must_change_password, "
            "created_at, last_login_at FROM admin_users WHERE username = ?",
            (username,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def verify_login(username: str, password: str):
    user = get_user(username)
    if not user:
        return None
    if not check_password_hash(user["password_hash"], password or ""):
        return None
    return user


def touch_login(username: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE admin_users SET last_login_at = ? WHERE username = ?",
            (datetime.now().isoformat(), username),
        )
        conn.commit()
    finally:
        conn.close()


def change_password(username: str, new_password: str) -> bool:
    password_hash = generate_password_hash(new_password, method="pbkdf2:sha256")
    conn = _connect()
    try:
        cur = conn.execute(
            "UPDATE admin_users SET password_hash = ?, must_change_password = 0 "
            "WHERE username = ?",
            (password_hash, username),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def audit(username: str, action: str, target: str = "", detail: str = "") -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO admin_audit_log (username, action, target, detail, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                (username or "")[:60],
                (action or "unknown")[:60],
                (target or "")[:200],
                (detail or "")[:500],
                datetime.now().isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def list_audit(limit: int = 20) -> list:
    try:
        limit = min(100, max(1, int(limit)))
    except (TypeError, ValueError):
        limit = 20
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, username, action, target, detail, created_at "
            "FROM admin_audit_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
