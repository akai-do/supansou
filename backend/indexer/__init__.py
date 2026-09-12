"""
indexer —— 历史索引模块（你的核心创新）

当年度盘搜的核心能力是"自建索引库"，他们靠爬虫。
你的索引库靠的是"每次搜索结果的副产品" + "定时收割"。

每次用户搜索后，结果自动写入本地 SQLite 索引。
索引库积累到一定量后，搜索时先查本地索引（毫秒级），再查 PanSou（秒级），
两者融合后返回 —— 实现"老的搜得到，新的也搜得到"。
"""
import os
import sqlite3
import time
import threading
from datetime import datetime, timedelta
from typing import Optional
from config import Config


class IndexDatabase:
    """索引数据库 —— SQLite 封装"""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or Config.INDEX_DB_PATH
        self._local = threading.local()
        self._init_db()

    @property
    def _conn(self):
        """线程本地连接"""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA cache_size=-8000")
        return self._local.conn

    def _init_db(self):
        """初始化表结构（线程安全，只在首次创建时执行）"""
        # fresh clone 时 backend/data/ 不存在（被 .gitignore 忽略），
        # sqlite3.connect 不会自动建目录，必须先创建
        data_dir = os.path.dirname(os.path.abspath(self.db_path))
        os.makedirs(data_dir, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS link_index (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                url         TEXT NOT NULL UNIQUE,
                password    TEXT DEFAULT '',
                title       TEXT DEFAULT '',
                disk_type   TEXT DEFAULT '',
                source      TEXT DEFAULT '',
                first_seen  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                checked_at  TIMESTAMP,
                alive       INTEGER DEFAULT 1,
                search_cnt  INTEGER DEFAULT 1
            );
            CREATE INDEX IF NOT EXISTS idx_url ON link_index(url);
            CREATE INDEX IF NOT EXISTS idx_disk_type ON link_index(disk_type);
            CREATE INDEX IF NOT EXISTS idx_alive ON link_index(alive);
            CREATE INDEX IF NOT EXISTS idx_last_seen ON link_index(last_seen);
            CREATE INDEX IF NOT EXISTS idx_title ON link_index(title);

            CREATE TABLE IF NOT EXISTS search_history (
                keyword TEXT PRIMARY KEY,
                cnt     INTEGER DEFAULT 1,
                last_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.commit()
        conn.close()

    def store_links(self, links: list) -> int:
        """
        批量写入索引（UPSERT 语义）

        参数:
            links: [{"url": "...", "password": "...", "title": "...",
                     "disk_type": "baidu", "source": "tg:频道名"}]

        返回:
            本次写入/更新的条数
        """
        if not links:
            return 0

        conn = self._conn
        now = datetime.now().isoformat()
        count = 0

        for link in links:
            url = link.get("url", "").strip()
            if not url:
                continue

            # 标题清洗：只保留首行、截断超长文本，避免污染索引与搜索结果展示
            title = (link.get("title", "") or "").strip()
            first_line = title.split("\n", 1)[0].strip()
            if len(first_line) > 120:
                first_line = first_line[:120] + "…"
            title = first_line
            password = (link.get("password", "") or "").strip()
            disk_type = (link.get("disk_type", "") or "").strip()
            source = (link.get("source", "") or "").strip()

            try:
                conn.execute("""
                    INSERT INTO link_index (url, password, title, disk_type,
                                            source, first_seen, last_seen, search_cnt)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(url) DO UPDATE SET
                        last_seen = ?,
                        search_cnt = search_cnt + 1,
                        title = CASE WHEN ? != '' THEN ? ELSE title END,
                        password = CASE WHEN ? != '' THEN ? ELSE password END
                """, (
                    url,
                    password,
                    title,
                    disk_type,
                    source,
                    now, now,
                    now,
                    title, title,
                    password, password,
                ))
                count += 1
            except sqlite3.IntegrityError:
                continue

        conn.commit()
        return count

    def search(self, keyword: str, disk_types: list = None,
               limit: int = 50, only_alive: bool = True) -> list:
        """
        在历史索引库中搜索

        这是"搜老"的核心 —— 直接在本地 SQLite 索引中做 LIKE 匹配。
        结果按 alive 优先、last_seen 倒序排列。

        参数:
            keyword: 搜索关键词
            disk_types: 网盘类型过滤（None 表示所有）
            limit: 最多返回条数
            only_alive: 是否只返回存活的链接

        返回:
            [{"id": 1, "url": "...", ...}]
        """
        # 中文智能分词：把连续关键词拆成多个子词，做 OR 模糊匹配。
        # 例："蜘蛛侠电影" → 同时匹配含"蜘蛛侠"或"电影"的标题，
        # 完整词命中得分更高（度盘搜风格的历史模糊检索）。
        keyword = (keyword or "").strip()
        if not keyword:
            return []

        tokens = self._tokenize(keyword)

        # 组装 WHERE 条件：token 之间 OR。
        # 短 token（<4 字符）只匹配标题——网盘分享码是随机字符串，
        # 短子串碰上 URL 会捞回大量无关行（垃圾查询的噪声来源）。
        like_conds = []
        like_params = []
        for tok in tokens:
            if len(tok) >= 4:
                like_conds.append("(title LIKE ? OR url LIKE ?)")
                like_params.extend([f"%{tok}%", f"%{tok}%"])
            else:
                like_conds.append("title LIKE ?")
                like_params.append(f"%{tok}%")

        conditions = ["(" + " OR ".join(like_conds) + ")"]
        params = list(like_params)

        if disk_types:
            placeholders = ",".join(["?" for _ in disk_types])
            conditions.append(f"disk_type IN ({placeholders})")
            params.extend(disk_types)

        if only_alive:
            conditions.append("alive = 1")

        # 完整词命中加分（用于排序）：额外消耗 2 个 ? 参数
        order_expr = "CASE WHEN title LIKE ? OR url LIKE ? THEN 1 ELSE 0 END DESC"
        order_params = [f"%{keyword}%", f"%{keyword}%"]

        sql = f"""
            SELECT id, url, password, title, disk_type, source,
                   first_seen, last_seen, checked_at, alive, search_cnt
            FROM link_index
            WHERE {' AND '.join(conditions)}
            ORDER BY alive DESC, {order_expr}, search_cnt DESC, last_seen DESC
            LIMIT ?
        """
        params += order_params           # ORDER BY 的全词命中两个参数
        params.append(limit)             # LIMIT

        conn = self._conn
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def _tokenize(self, keyword: str) -> list:
        """
        轻量中文分词：无第三方依赖。
        - 对中文：按 2-3 字滑动窗口切出有意义的词
        - 对 ASCII/数字：作为一个整体保留
        - 返回去重的词列表
        """
        tokens = []
        ascii_buf = []

        def flush_ascii():
            nonlocal ascii_buf
            if ascii_buf:
                w = "".join(ascii_buf).lower()
                if w not in tokens:
                    tokens.append(w)
                ascii_buf = []

        i = 0
        chars = list(keyword)
        n = len(chars)
        while i < n:
            c = chars[i]
            if ord(c) < 128:  # ASCII
                ascii_buf.append(c)
                i += 1
                continue
            flush_ascii()
            # 中文：收集连续中文字符段
            seg_start = i
            while i < n and ord(chars[i]) >= 128:
                i += 1
            seg = "".join(chars[seg_start:i])
            self._add_chinese_tokens(tokens, seg)
        flush_ascii()

        # 同时保留完整词本身
        if keyword not in tokens:
            tokens.insert(0, keyword)
        return tokens

    def _add_chinese_tokens(self, tokens: list, seg: str):
        """对一段连续中文做 2-3 字滑动窗口切词并去重"""
        L = len(seg)
        if L <= 3:
            if seg not in tokens:
                tokens.append(seg)
            return
        # 2字窗口
        for i in range(L - 1):
            tok = seg[i:i+2]
            if tok not in tokens:
                tokens.append(tok)
        # 3字窗口
        for i in range(L - 2):
            tok = seg[i:i+3]
            if tok not in tokens:
                tokens.append(tok)

    def update_alive_status(self, url: str, alive: bool):
        """更新单条链接的存活状态"""
        self._conn.execute(
            "UPDATE link_index SET alive = ?, checked_at = ? WHERE url = ?",
            (1 if alive else 0, datetime.now().isoformat(), url)
        )
        self._conn.commit()

    # ==========================================
    # 搜索历史
    # ==========================================

    def record_search(self, keyword: str):
        """记录一次搜索（UPSERT：重复搜索累计次数并刷新时间）"""
        keyword = (keyword or "").strip()
        if not keyword:
            return
        now = datetime.now().isoformat()
        self._conn.execute("""
            INSERT INTO search_history (keyword, cnt, last_at)
            VALUES (?, 1, ?)
            ON CONFLICT(keyword) DO UPDATE SET
                cnt = cnt + 1,
                last_at = ?
        """, (keyword, now, now))
        self._conn.commit()

    def get_history(self, limit: int = 20) -> list:
        """最近搜索历史（按时间倒序）"""
        rows = self._conn.execute(
            "SELECT keyword, cnt, last_at FROM search_history "
            "ORDER BY last_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_history(self, keyword: str = None) -> int:
        """删除搜索历史：keyword 为空则清空全部，返回删除条数"""
        if keyword:
            cur = self._conn.execute(
                "DELETE FROM search_history WHERE keyword = ?", (keyword,)
            )
        else:
            cur = self._conn.execute("DELETE FROM search_history")
        self._conn.commit()
        return cur.rowcount

    def get_unchecked_links(self, days: int = 7, limit: int = 100) -> list:
        """获取超过 N 天未检测的链接"""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        rows = self._conn.execute("""
            SELECT id, url, password, title, disk_type, source
            FROM link_index
            WHERE checked_at IS NULL OR checked_at < ?
            ORDER BY checked_at ASC NULLS FIRST
            LIMIT ?
        """, (cutoff, limit)).fetchall()
        return [dict(r) for r in rows]

    def get_stats(self) -> dict:
        """获取索引库统计"""
        conn = self._conn
        total = conn.execute("SELECT COUNT(*) FROM link_index").fetchone()[0]
        alive = conn.execute("SELECT COUNT(*) FROM link_index WHERE alive=1").fetchone()[0]
        dead = conn.execute("SELECT COUNT(*) FROM link_index WHERE alive=0").fetchone()[0]

        # 按网盘类型统计
        type_rows = conn.execute(
            "SELECT disk_type, COUNT(*) as cnt FROM link_index GROUP BY disk_type ORDER BY cnt DESC"
        ).fetchall()
        by_type = {r["disk_type"]: r["cnt"] for r in type_rows}

        # 搜索热词榜（按 search_cnt 排）
        hot_rows = conn.execute(
            "SELECT title, search_cnt FROM link_index WHERE title != '' ORDER BY search_cnt DESC LIMIT 20"
        ).fetchall()
        hot_keywords = [{"title": r["title"], "count": r["search_cnt"]} for r in hot_rows]

        return {
            "total": total,
            "alive": alive,
            "dead": dead,
            "by_type": by_type,
            "hot_keywords": hot_keywords,
        }

    def close(self):
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None