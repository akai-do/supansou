"""站点公开配置与公告。

设计约束：
  * 只新增 `site_settings` / `notices` 两张表，绝不修改现有表结构；
  * 公开接口只返回安全字段（站点名、公告、页脚、功能开关），
    不返回 token / 账号 / Cookie / 管理配置；
  * 读取失败时回退到 Config 默认值，不影响搜索主流程。
"""
import os
import sqlite3
import threading
from datetime import datetime

from config import Config

_LOCK = threading.Lock()

# 公开配置默认值。这些值会写入 site_settings（INSERT OR IGNORE），
# 后续可由后台 / 手工改库覆盖。
DEFAULTS = {
    "site_name": Config.SITE_NAME,
    "site_subtitle": Config.SITE_SUBTITLE,
    "site_logo": Config.SITE_LOGO,
    "footer_note": "本站仅提供资源索引与链接有效性检测，不存储任何文件。",
    "repo_url": os.getenv("GITHUB_REPO_URL", "https://github.com/akai-do/supansou"),
    "notice_enabled": "1" if Config.SITE_NOTICE_ENABLED else "0",
    "notice_text": Config.SITE_NOTICE_TEXT,
    "notice_version": Config.SITE_NOTICE_VERSION,
    "legal_footer": "1",
}


def _connect():
    conn = sqlite3.connect(Config.INDEX_DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def ensure_schema() -> None:
    """只建新表 + 写入默认值；幂等，可重复调用。"""
    with _LOCK:
        conn = _connect()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS site_settings (
                    key        TEXT PRIMARY KEY,
                    value      TEXT NOT NULL DEFAULT '',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS notices (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    content    TEXT NOT NULL,
                    version    TEXT NOT NULL,
                    enabled    INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            now = datetime.now().isoformat()
            for key, value in DEFAULTS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO site_settings (key, value, updated_at) "
                    "VALUES (?, ?, ?)",
                    (key, value, now),
                )
            # 公告表只播种一条；后续版本通过后台新增，旧版本保留可追溯
            if conn.execute("SELECT COUNT(*) FROM notices").fetchone()[0] == 0:
                conn.execute(
                    "INSERT INTO notices (content, version, enabled, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        DEFAULTS["notice_text"],
                        DEFAULTS["notice_version"],
                        1 if Config.SITE_NOTICE_ENABLED else 0,
                        now,
                    ),
                )
            conn.commit()
        finally:
            conn.close()


def get_settings() -> dict:
    """读取全部站点配置；缺失项用默认值补齐。"""
    out = dict(DEFAULTS)
    conn = None
    try:
        conn = _connect()
        rows = conn.execute("SELECT key, value FROM site_settings").fetchall()
        for row in rows:
            out[row["key"]] = row["value"]
    except Exception:
        # 配置读取失败不应影响页面与搜索
        pass
    finally:
        if conn is not None:
            conn.close()
    return out


def demand_enabled() -> bool:
    """需求板是否生效。

    环境变量 DEMAND_ENABLED 是总闸；数据库 `demand_enabled` 是后台二级开关。
    未写入二级开关时跟随总闸（env=1 即开启），后台关闭时写入 "0"。
    """
    if not Config.DEMAND_ENABLED:
        return False
    value = get_settings().get("demand_enabled")
    if value is None or str(value).strip() == "":
        return True
    return str(value) == "1"


def notice_enabled() -> bool:
    """公告是否生效：环境变量总闸 + 数据库开关都为 1。"""
    if not Config.SITE_NOTICE_ENABLED:
        return False
    return str(get_settings().get("notice_enabled", "0")) == "1"


def set_setting(key: str, value: str) -> None:
    """写入单个配置项（后续后台使用；当前阶段只做安全 upsert）。"""
    key = (key or "").strip()
    if not key:
        return
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO site_settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (key, str(value), datetime.now().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def get_notice() -> dict:
    """当前公告。`notices` 最新启用版本优先，退回 site_settings。"""
    settings = get_settings()
    enabled = str(settings.get("notice_enabled", "0")) == "1" \
        and Config.SITE_NOTICE_ENABLED
    content = settings.get("notice_text", "")
    version = settings.get("notice_version", "")

    conn = None
    try:
        conn = _connect()
        row = conn.execute(
            "SELECT content, version FROM notices "
            "WHERE enabled = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row and row["content"]:
            content = row["content"]
            version = row["version"] or version
    except Exception:
        pass
    finally:
        if conn is not None:
            conn.close()

    return {
        "enabled": bool(enabled and content.strip()),
        "content": content.strip() if content else "",
        "version": version or "",
    }


def public_settings() -> dict:
    """公开安全字段。任何 token / 账号 / Cookie 都不得进入这里。"""
    settings = get_settings()
    return {
        "site": {
            "name": settings.get("site_name") or Config.SITE_NAME,
            "subtitle": settings.get("site_subtitle") or Config.SITE_SUBTITLE,
            "logo": settings.get("site_logo") or Config.SITE_LOGO,
        },
        "notice": get_notice(),
        "footer": {
            "legal": str(settings.get("legal_footer", "1")) == "1",
            "note": settings.get("footer_note", ""),
            "repo_url": settings.get("repo_url", DEFAULTS["repo_url"]),
        },
        "features": {
            "aliso_ui": Config.ALISO_UI,
            "stream_search": Config.STREAM_SEARCH,
            "demand_enabled": demand_enabled(),
            "admin_v2": Config.ADMIN_V2,
            "site_notice_enabled": notice_enabled(),
        },
    }
