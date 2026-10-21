"""需求板数据层。

只新增 `demands` / `demand_votes` 两张表，不改旧表。
公开列表永远不返回联系方式；联系方式只给后台审核使用。
"""
import hashlib
import json
import re
import sqlite3
import threading
import time
from datetime import datetime

from config import Config

_LOCK = threading.Lock()
_RATE_BUCKETS = {}

# 公开门面的基础防刷：明显的垃圾/招嫖/违法词直接拒绝。
_DENY_WORDS = (
    "代考", "办证", "博彩", "赌博", "色情", "成人", "约炮", "人妻",
    "迷药", "枪支", "毒品", "发票", "贷款", "加微信", "加QQ", "裸聊",
    "援交", "一夜情", "福利姬", "嫖", "淫",
)

_STATUSES = ("pending", "resolved", "rejected")


def _connect():
    conn = sqlite3.connect(Config.INDEX_DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def ensure_schema():
    conn = _connect()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS demands (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                title         TEXT NOT NULL,
                platforms     TEXT DEFAULT '[]',
                play_url      TEXT DEFAULT '',
                remark        TEXT DEFAULT '',
                contact       TEXT DEFAULT '',
                is_paid       INTEGER DEFAULT 0,
                status        TEXT DEFAULT 'pending',
                resolve_note  TEXT DEFAULT '',
                reject_reason TEXT DEFAULT '',
                want_count    INTEGER DEFAULT 0,
                deleted       INTEGER DEFAULT 0,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_demands_status ON demands(status);
            CREATE INDEX IF NOT EXISTS idx_demands_created ON demands(created_at);

            CREATE TABLE IF NOT EXISTS demand_votes (
                demand_id INTEGER NOT NULL,
                voter_key TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (demand_id, voter_key)
            );

            CREATE TABLE IF NOT EXISTS demand_comments (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                demand_id  INTEGER NOT NULL,
                nickname   TEXT DEFAULT '',
                content    TEXT NOT NULL,
                voter_key  TEXT DEFAULT '',
                is_admin   INTEGER DEFAULT 0,
                status     TEXT DEFAULT 'visible',
                deleted    INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_demand_comments_demand
                ON demand_comments(demand_id, status, deleted);
        """)
        conn.commit()
    finally:
        conn.close()


def voter_key(request) -> str:
    """匿名指纹：IP + User-Agent 的哈希，不存明文 IP。"""
    ip = request.remote_addr or ""
    ua = (request.headers.get("User-Agent") or "")[:200]
    salt = Config.ADMIN_SECRET_KEY or Config.SITE_NAME
    raw = f"{salt}|{ip}|{ua}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


def allow_rate(ip: str, kind: str, limit: int, window_seconds: int) -> bool:
    """简单的内存滑动窗口限流（单进程有效；重启清零）。"""
    if limit <= 0:
        return True
    now = time.time()
    key = (kind, ip or "?")
    with _LOCK:
        arr = [t for t in _RATE_BUCKETS.get(key, []) if now - t < window_seconds]
        if len(arr) >= limit:
            _RATE_BUCKETS[key] = arr
            return False
        arr.append(now)
        _RATE_BUCKETS[key] = arr
        return True


def normalize_platforms(value) -> list:
    if isinstance(value, str):
        parts = re.split(r"[,，、;；\s]+", value)
    elif isinstance(value, list):
        parts = [str(x) for x in value]
    else:
        parts = []
    out = []
    for part in parts:
        text = part.strip()
        if text and text not in out:
            out.append(text[:20])
    return out[:6]


def validate_demand(data: dict):
    """返回 (clean, error)。clean 中的字段已裁剪、校验。"""
    if not isinstance(data, dict):
        return None, "请求格式不正确"

    title = re.sub(r"\s+", " ", str(data.get("title") or "")).strip()
    if len(title) < 2:
        return None, "资源名称至少 2 个字"
    if len(title) > 80:
        return None, "资源名称最多 80 个字"

    platforms = normalize_platforms(data.get("platforms"))
    if not platforms:
        return None, "请至少选择一个播放平台"

    play_url = str(data.get("play_url") or "").strip()
    if play_url:
        if len(play_url) > 500:
            return None, "播放地址过长"
        if not re.match(r"^https?://", play_url, re.IGNORECASE):
            return None, "播放地址只支持 http(s) 链接"

    remark = str(data.get("remark") or "").strip()
    if len(remark) > 500:
        return None, "备注最多 500 个字"

    contact = str(data.get("contact") or "").strip()
    if len(contact) > 100:
        return None, "联系方式最多 100 个字"

    blob = f"{title} {remark} {contact}".lower()
    if any(word.lower() in blob for word in _DENY_WORDS):
        return None, "内容包含不允许的词语"

    return {
        "title": title,
        "platforms": json.dumps(platforms, ensure_ascii=False),
        "play_url": play_url,
        "remark": remark,
        "contact": contact,
        "is_paid": 1 if data.get("is_paid") else 0,
    }, None


def validate_comment(data: dict):
    """评论入参校验。昵称可空，空时统一显示“热心网友”。"""
    if not isinstance(data, dict):
        return None, "请求格式不正确"

    nickname = re.sub(r"\s+", " ", str(data.get("nickname") or "")).strip()
    if len(nickname) > 20:
        return None, "昵称最多 20 个字"

    content = re.sub(r"\s+", " ", str(data.get("content") or "")).strip()
    if not content:
        return None, "评论内容不能为空"
    if len(content) > 500:
        return None, "评论最多 500 个字"

    blob = f"{nickname} {content}".lower()
    if any(word.lower() in blob for word in _DENY_WORDS):
        return None, "评论包含不允许的词语"

    return {
        "nickname": nickname or "热心网友",
        "content": content,
    }, None


def create_demand(clean: dict) -> int:
    now = datetime.now().isoformat()
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO demands (title, platforms, play_url, remark, contact, "
            "is_paid, status, want_count, deleted, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, 0, ?, ?)",
            (
                clean["title"], clean["platforms"], clean["play_url"],
                clean["remark"], clean["contact"], clean["is_paid"], now, now,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def _public_item(row, wanted: bool) -> dict:
    try:
        platforms = json.loads(row["platforms"] or "[]")
        if not isinstance(platforms, list):
            platforms = []
    except (ValueError, TypeError):
        platforms = []
    try:
        comment_count = int(row["comment_count"] or 0)
    except (IndexError, KeyError):
        comment_count = 0
    return {
        "id": row["id"],
        "title": row["title"],
        "platforms": platforms,
        "play_url": row["play_url"] or "",
        "remark": row["remark"] or "",
        "is_paid": bool(row["is_paid"]),
        "status": row["status"] or "pending",
        "resolve_note": row["resolve_note"] or "",
        "reject_reason": row["reject_reason"] or "",
        "want_count": int(row["want_count"] or 0),
        "wanted": bool(wanted),
        "comment_count": comment_count,
        "created_at": row["created_at"] or "",
    }


def list_public(sort: str = "want", page: int = 1, page_size: int = 20,
                vk: str = "") -> dict:
    sort = "time" if str(sort).lower() == "time" else "want"
    try:
        page = max(1, int(page))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = min(50, max(1, int(page_size)))
    except (TypeError, ValueError):
        page_size = 20

    order = ("want_count DESC, created_at DESC" if sort == "want"
             else "created_at DESC")
    conn = _connect()
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM demands WHERE deleted = 0"
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT d.*, EXISTS("
            "  SELECT 1 FROM demand_votes v "
            "  WHERE v.demand_id = d.id AND v.voter_key = ?"
            ") AS wanted, "
            "(SELECT COUNT(*) FROM demand_comments c "
            " WHERE c.demand_id = d.id AND c.status = 'visible' "
            " AND c.deleted = 0) AS comment_count "
            "FROM demands d WHERE d.deleted = 0 "
            f"ORDER BY {order} LIMIT ? OFFSET ?",
            (vk, page_size, (page - 1) * page_size),
        ).fetchall()
        items = [_public_item(r, bool(r["wanted"])) for r in rows]
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": max(1, (total + page_size - 1) // page_size),
            "sort": sort,
        }
    finally:
        conn.close()


def toggle_vote(demand_id: int, vk: str) -> dict:
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        demand = conn.execute(
            "SELECT id FROM demands WHERE id = ? AND deleted = 0", (demand_id,)
        ).fetchone()
        if not demand:
            conn.rollback()
            return {"error": "需求不存在或已删除", "status": 404}

        vote = conn.execute(
            "SELECT 1 FROM demand_votes WHERE demand_id = ? AND voter_key = ?",
            (demand_id, vk),
        ).fetchone()
        if vote:
            conn.execute(
                "DELETE FROM demand_votes WHERE demand_id = ? AND voter_key = ?",
                (demand_id, vk),
            )
            wanted = False
        else:
            conn.execute(
                "INSERT INTO demand_votes (demand_id, voter_key, created_at) "
                "VALUES (?, ?, ?)",
                (demand_id, vk, datetime.now().isoformat()),
            )
            wanted = True

        count = conn.execute(
            "SELECT COUNT(*) FROM demand_votes WHERE demand_id = ?", (demand_id,)
        ).fetchone()[0]
        conn.execute(
            "UPDATE demands SET want_count = ?, updated_at = ? WHERE id = ?",
            (count, datetime.now().isoformat(), demand_id),
        )
        conn.commit()
        return {"wanted": wanted, "want_count": int(count)}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def counts() -> dict:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS c FROM demands WHERE deleted = 0 "
            "GROUP BY status"
        ).fetchall()
        out = {s: 0 for s in _STATUSES}
        for row in rows:
            if row["status"] in out:
                out[row["status"]] = int(row["c"])
        out["total"] = sum(out.values())
        return out
    finally:
        conn.close()


def list_admin(status: str = "", page: int = 1, page_size: int = 20) -> dict:
    status = status if status in _STATUSES else ""
    try:
        page = max(1, int(page))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = min(100, max(1, int(page_size)))
    except (TypeError, ValueError):
        page_size = 20

    where = ["deleted = 0"]
    params = []
    if status:
        where.append("status = ?")
        params.append(status)
    where_sql = " WHERE " + " AND ".join(where)

    conn = _connect()
    try:
        total = conn.execute(
            f"SELECT COUNT(*) FROM demands{where_sql}", params
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT d.*, "
            "(SELECT COUNT(*) FROM demand_comments c "
            " WHERE c.demand_id = d.id AND c.status = 'visible' "
            " AND c.deleted = 0) AS comment_count "
            f"FROM demands d{where_sql} ORDER BY d.created_at DESC "
            "LIMIT ? OFFSET ?",
            params + [page_size, (page - 1) * page_size],
        ).fetchall()
        items = []
        for row in rows:
            try:
                platforms = json.loads(row["platforms"] or "[]")
            except (ValueError, TypeError):
                platforms = []
            items.append({
                "id": row["id"],
                "title": row["title"],
                "platforms": platforms,
                "play_url": row["play_url"] or "",
                "remark": row["remark"] or "",
                "contact": row["contact"] or "",
                "is_paid": bool(row["is_paid"]),
                "status": row["status"] or "pending",
                "resolve_note": row["resolve_note"] or "",
                "reject_reason": row["reject_reason"] or "",
                "want_count": int(row["want_count"] or 0),
                "comment_count": int(row["comment_count"] or 0),
                "created_at": row["created_at"] or "",
                "updated_at": row["updated_at"] or "",
            })
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": max(1, (total + page_size - 1) // page_size),
            "counts": counts(),
        }
    finally:
        conn.close()


def update_admin(demand_id: int, status: str, resolve_note: str = "",
                 reject_reason: str = "") -> bool:
    if status not in _STATUSES:
        return False
    resolve_note = str(resolve_note or "").strip()[:300]
    reject_reason = str(reject_reason or "").strip()[:300]
    if status == "resolved":
        reject_reason = ""
    elif status == "rejected":
        resolve_note = ""
    else:
        resolve_note = reject_reason = ""

    conn = _connect()
    try:
        cur = conn.execute(
            "UPDATE demands SET status = ?, resolve_note = ?, reject_reason = ?, "
            "updated_at = ? WHERE id = ? AND deleted = 0",
            (status, resolve_note, reject_reason,
             datetime.now().isoformat(), demand_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def soft_delete(demand_id: int) -> bool:
    conn = _connect()
    try:
        cur = conn.execute(
            "UPDATE demands SET deleted = 1, updated_at = ? WHERE id = ? AND deleted = 0",
            (datetime.now().isoformat(), demand_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ==========================================
# 评论
# ==========================================
def _comment_item(row) -> dict:
    return {
        "id": row["id"],
        "demand_id": row["demand_id"],
        "nickname": row["nickname"] or "热心网友",
        "content": row["content"] or "",
        "is_admin": bool(row["is_admin"]),
        "status": row["status"] or "visible",
        "created_at": row["created_at"] or "",
    }


def list_comments(demand_id: int, include_hidden: bool = False) -> dict:
    conn = _connect()
    try:
        demand = conn.execute(
            "SELECT id FROM demands WHERE id = ? AND deleted = 0", (demand_id,)
        ).fetchone()
        if not demand:
            return {"error": "需求不存在或已删除", "status": 404}

        where = ["demand_id = ?", "deleted = 0"]
        if not include_hidden:
            where.append("status = 'visible'")
        rows = conn.execute(
            f"SELECT * FROM demand_comments WHERE {' AND '.join(where)} "
            "ORDER BY id ASC",
            (demand_id,),
        ).fetchall()
        return {
            "items": [_comment_item(r) for r in rows],
            "total": len(rows),
        }
    finally:
        conn.close()


def create_comment(demand_id: int, clean: dict, voter_key: str,
                   is_admin: bool = False) -> dict:
    now = datetime.now().isoformat()
    conn = _connect()
    try:
        demand = conn.execute(
            "SELECT id FROM demands WHERE id = ? AND deleted = 0", (demand_id,)
        ).fetchone()
        if not demand:
            return {"error": "需求不存在或已删除", "status": 404}
        cur = conn.execute(
            "INSERT INTO demand_comments "
            "(demand_id, nickname, content, voter_key, is_admin, status, deleted, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'visible', 0, ?)",
            (demand_id, clean["nickname"], clean["content"], voter_key,
             1 if is_admin else 0, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM demand_comments WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return {"item": _comment_item(row)}
    finally:
        conn.close()


def set_comment_status(comment_id: int, status: str) -> bool:
    if status not in ("visible", "hidden"):
        return False
    conn = _connect()
    try:
        cur = conn.execute(
            "UPDATE demand_comments SET status = ? WHERE id = ? AND deleted = 0",
            (status, comment_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_comment(comment_id: int) -> bool:
    conn = _connect()
    try:
        cur = conn.execute(
            "UPDATE demand_comments SET deleted = 1 WHERE id = ? AND deleted = 0",
            (comment_id,),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
