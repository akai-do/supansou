"""
indexer —— 历史索引模块（你的核心创新）

当年度盘搜的核心能力是"自建索引库"，他们靠爬虫。
你的索引库靠的是"每次搜索结果的副产品" + "定时收割"。

每次用户搜索后，结果自动写入本地 SQLite 索引。
索引库积累到一定量后，搜索时先查本地索引（毫秒级），再查 PanSou（秒级），
两者融合后返回 —— 实现"老的搜得到，新的也搜得到"。
"""
import logging
import os
import re
import sqlite3
import time
import threading
from datetime import date, datetime, timedelta
from typing import Optional

from config import Config
from source_stats import classify_category, today_str

logger = logging.getLogger("indexer")


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
            # 曝光/点击统计会在搜索线程与请求线程并发写入；等锁而不是立刻报错
            self._local.conn.execute("PRAGMA busy_timeout=3000")
        return self._local.conn

    def _init_db(self):
        """初始化表结构（线程安全，只在首次创建时执行）"""
        # fresh clone 时 backend/data/ 不存在（被 .gitignore 忽略），
        # sqlite3.connect 不会自动建目录，必须先创建
        data_dir = os.path.dirname(os.path.abspath(self.db_path))
        os.makedirs(data_dir, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
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
            CREATE INDEX IF NOT EXISTS idx_source ON link_index(source);

            CREATE TABLE IF NOT EXISTS search_history (
                keyword TEXT PRIMARY KEY,
                cnt     INTEGER DEFAULT 1,
                last_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS click_stats (
                url     TEXT NOT NULL,
                kw      TEXT NOT NULL DEFAULT '',
                source  TEXT DEFAULT '',
                cnt     INTEGER DEFAULT 1,
                last_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (url, kw)
            );

            -- 来源质量：按“天 + 来源 + 关键词”聚合曝光/点击，
            -- 既支持 7/30 天窗口，也避免为每次展示存一行明细。
            CREATE TABLE IF NOT EXISTS source_daily_stats (
                day         TEXT NOT NULL,
                category    TEXT NOT NULL DEFAULT '',
                source      TEXT NOT NULL DEFAULT '',
                kw          TEXT NOT NULL DEFAULT '',
                impressions INTEGER DEFAULT 0,
                clicks      INTEGER DEFAULT 0,
                PRIMARY KEY (day, source, kw)
            );
            CREATE INDEX IF NOT EXISTS idx_source_daily_day
                ON source_daily_stats(day);

            CREATE TABLE IF NOT EXISTS zero_result_watch (
                kw       TEXT PRIMARY KEY,
                cnt      INTEGER DEFAULT 1,
                last_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS poster_cache (
                key        TEXT PRIMARY KEY,
                img        TEXT DEFAULT '',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # 轻量迁移：老库补列（智能有效性状态机字段）
        cols = {r[1] for r in conn.execute("PRAGMA table_info(link_index)")}
        for ddl in (
            "ALTER TABLE link_index ADD COLUMN validity TEXT DEFAULT ''",
            "ALTER TABLE link_index ADD COLUMN fail_cnt INTEGER DEFAULT 0",
            "ALTER TABLE link_index ADD COLUMN ok_cnt INTEGER DEFAULT 0",
            "ALTER TABLE link_index ADD COLUMN state_summary TEXT DEFAULT ''",
            "ALTER TABLE link_index ADD COLUMN msg_image TEXT DEFAULT ''",
            "ALTER TABLE link_index ADD COLUMN category TEXT DEFAULT ''",
        ):
            col = ddl.split("ADD COLUMN ")[1].split()[0]
            if col not in cols:
                conn.execute(ddl)

        # 点击统计补来源列：新点击直接写 source，旧点击留空、查询时回退
        # 到 link_index 反查（COALESCE），不阻塞升级。
        click_cols = {r[1] for r in conn.execute("PRAGMA table_info(click_stats)")}
        if "source" not in click_cols:
            conn.execute("ALTER TABLE click_stats ADD COLUMN source TEXT DEFAULT ''")

        # 旧版 alive=0 但 validity 为空的行，统一迁到四态里的 dead。
        # 这样“未检测”只表示真的没有四态结论，不再把旧死链混进未检测。
        conn.execute(
            "UPDATE link_index SET validity='dead', "
            "checked_at=COALESCE(checked_at, last_seen) "
            "WHERE (validity IS NULL OR validity='') AND alive=0"
        )

        # 类别回填：老库没有 category 列，首次升级时按标题归类写一遍。
        # 之后新入库由 store_links 写入，category='' 的行数为 0，不再扫描。
        pending = conn.execute(
            "SELECT COUNT(*) FROM link_index "
            "WHERE category IS NULL OR category = ''"
        ).fetchone()[0]
        if pending:
            cursor = conn.execute(
                "SELECT id, title FROM link_index "
                "WHERE category IS NULL OR category = ''"
            )
            while True:
                batch = cursor.fetchmany(5000)
                if not batch:
                    break
                conn.executemany(
                    "UPDATE link_index SET category = ? WHERE id = ?",
                    [(classify_category(r["title"]), r["id"]) for r in batch],
                )

        conn.commit()
        conn.close()

    def iter_titles_for_vocab(self, limit: int = 80000):
        """标题迭代器（供纠错词库构建；分批拉取避免一次性占内存）"""
        conn = self._conn
        cur = conn.execute(
            "SELECT title FROM link_index WHERE title != '' LIMIT ?", (limit,)
        )
        while True:
            rows = cur.fetchmany(2000)
            if not rows:
                break
            for r in rows:
                yield r["title"]

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
            category = classify_category(title)

            try:
                msg_image = (link.get("msg_image") or "").strip()
                conn.execute("""
                    INSERT INTO link_index (url, password, title, disk_type,
                                            source, category, first_seen, last_seen,
                                            search_cnt, msg_image)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                    ON CONFLICT(url) DO UPDATE SET
                        last_seen = ?,
                        search_cnt = search_cnt + 1,
                        title = CASE WHEN ? != '' THEN ? ELSE title END,
                        password = CASE WHEN ? != '' THEN ? ELSE password END,
                        msg_image = CASE WHEN ? != '' THEN ? ELSE msg_image END,
                        category = CASE WHEN ? != '' THEN ? ELSE category END
                """, (
                    url,
                    password,
                    title,
                    disk_type,
                    source,
                    category,
                    now, now,
                    msg_image,
                    now,
                    title, title,
                    password, password,
                    msg_image, msg_image,
                    category, category,
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
        # 排序优先级词：短查询用完整关键词；长查询用最具体的 CJK 核心词
        # （如 2026考研英语一真题解析 → 考研英语），避免“2026”“国语”
        # 这类泛词把候选池前排占满。
        priority_token = keyword

        # 长查询精准模式：>8 字（长书名/教材名）时不用 CJK 2-3 字滑窗——
        # "python与人工智能编程-基础与实践" 的"基础""实践"窗口会把
        # 海量无关行捞进来。只保留完整词 + ASCII 词（>=4 字）。
        if len(keyword) > 8:
            ascii_tokens = [t for t in tokens
                            if all(ord(c) < 128 for c in t) and len(t) >= 4]
            # 长查询的 CJK 段/前 4 字也参与 SQL 召回：
            # 只认完整关键词会让索引里的“考研英语一、二真题解析”
            # 无法进入候选池。精确性仍由 API 层 _matches_query 把关。
            core_tokens = []
            for seg in re.findall(r"[\u4e00-\u9fff]{2,}", keyword):
                if seg not in core_tokens:
                    core_tokens.append(seg)
                head = seg[:4]
                if len(head) >= 2 and head not in core_tokens:
                    core_tokens.append(head)
            tokens = [keyword] + [
                t for t in ascii_tokens + core_tokens if t != keyword]
            # 选最短的 >=4 字核心词当排序优先级（4 字头部最具体）
            candidates = [t for t in core_tokens if len(t) >= 4]
            if candidates:
                priority_token = min(candidates, key=len)

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

        # 完整词命中优先；长查询再用核心词（如“考研英语”）优先，
        # 否则候选池前 80 条会被“2026”“国语”这类泛词占满。
        order_expr = (
            "CASE WHEN title LIKE ? OR url LIKE ? THEN 1 ELSE 0 END DESC, "
            "CASE WHEN title LIKE ? THEN 1 ELSE 0 END DESC"
        )
        order_params = [
            f"%{keyword}%", f"%{keyword}%", f"%{priority_token}%",
        ]

        sql = f"""
            SELECT id, url, password, title, disk_type, source,
                   first_seen, last_seen, checked_at, alive, search_cnt,
                   validity, fail_cnt, ok_cnt, state_summary, msg_image
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

    # ==========================================
    # 智能有效性状态机
    # PanSou 检测状态 → validity 映射：
    #   ok        → 'ok'      有效
    #   bad       → 连续 DEAD_CONFIRM_STREAK 次才置 'dead'，首次为 'suspect'
    #   uncertain → 'suspect' 检测源要求验证，不武断判死，仅降权
    #   unsupported → 不改状态（磁力/ed2k 等无法检测）
    # ==========================================

    def apply_check_result(self, url: str, state: str, summary: str = "") -> dict:
        """
        将一次检测结果写入状态机（智能有效性检测的核心写入点）。

        返回 {"validity": ..., "alive": ...}；URL 不在库中返回 validity=None。
        """
        conn = self._conn
        row = conn.execute(
            "SELECT validity, alive, fail_cnt, ok_cnt FROM link_index WHERE url = ?",
            (url,)
        ).fetchone()
        if row is None:
            return {"validity": None, "alive": None}

        validity = row["validity"] or ""
        alive = row["alive"]
        fail_cnt = row["fail_cnt"] or 0
        ok_cnt = row["ok_cnt"] or 0
        now = datetime.now().isoformat()

        if state == "ok":
            ok_cnt += 1
            fail_cnt = 0
            validity = "ok"
            alive = 1
        elif state == "bad":
            fail_cnt += 1
            ok_cnt = 0
            if fail_cnt >= Config.DEAD_CONFIRM_STREAK:
                validity = "dead"
                alive = 0
            else:
                # 首次失败先按疑似处理，等待复检确认，防误杀
                validity = "suspect"
                alive = 1
        elif state == "uncertain":
            ok_cnt = 0
            validity = "suspect"
            alive = 1
        else:
            # unsupported / 未知状态：不动状态机，仅刷新检测时间
            conn.execute(
                "UPDATE link_index SET checked_at = ? WHERE url = ?",
                (now, url)
            )
            conn.commit()
            return {"validity": validity, "alive": alive}

        conn.execute(
            "UPDATE link_index SET validity = ?, alive = ?, fail_cnt = ?, ok_cnt = ?, "
            "state_summary = ?, checked_at = ? WHERE url = ?",
            (validity, alive, fail_cnt, ok_cnt, (summary or "")[:60], now, url)
        )
        conn.commit()
        return {"validity": validity, "alive": alive}

    def mark_reported_dead(self, url: str) -> bool:
        """
        用户举报失效：直接确认（用户亲眼所见优先于检测源），计数 +1 供后续复查参考。
        URL 不在库中则补一条占位记录，保证举报不丢。
        """
        conn = self._conn
        cur = conn.execute(
            "UPDATE link_index SET validity = 'dead', alive = 0, fail_cnt = fail_cnt + 1, "
            "ok_cnt = 0, state_summary = '用户举报失效', checked_at = ? WHERE url = ?",
            (datetime.now().isoformat(), url)
        )
        if cur.rowcount == 0:
            conn.execute(
                "INSERT INTO link_index (url, validity, alive, fail_cnt, ok_cnt, "
                "state_summary, checked_at) VALUES (?, 'dead', 0, 1, 0, '用户举报失效', ?)",
                (url, datetime.now().isoformat())
            )
        conn.commit()
        return True

    def get_validity_map(self, urls: list) -> dict:
        """
        批量读取链接的当前有效性状态。
        返回 {url: {"validity", "alive", "fail_cnt", "state_summary", "checked_at"}}。
        老数据 validity 为空但 alive=0 的行按 'dead' 返回。
        """
        out = {}
        if not urls:
            return out
        conn = self._conn
        for i in range(0, len(urls), 500):
            chunk = [u for u in urls[i:i + 500] if u]
            if not chunk:
                continue
            ph = ",".join(["?" for _ in chunk])
            rows = conn.execute(
                f"SELECT url, validity, alive, fail_cnt, state_summary, checked_at "
                f"FROM link_index WHERE url IN ({ph})", chunk
            ).fetchall()
            for r in rows:
                v = r["validity"] or ("dead" if r["alive"] == 0 else "")
                out[r["url"]] = {
                    "validity": v,
                    "alive": r["alive"],
                    "fail_cnt": r["fail_cnt"] or 0,
                    "state_summary": r["state_summary"] or "",
                    "checked_at": r["checked_at"] or "",
                }
        return out

    def get_smart_check_candidates(self, limit: int = 200) -> list:
        """
        智能巡检候选：按优先级取"需要检测"的链接。
          0. 疑似失效（复检窗口最短，尽快确认或洗白）
          1. 从未检测的新链接（越新越可能被用户使用）
          2. 确认失效但超过复查 TTL（探测恢复）
          3. 有效但超过保鲜 TTL 的
        磁力/ed2k 等检测源不支持的类型直接跳过。
        """
        now = datetime.now()
        dead_cutoff = (now - timedelta(hours=Config.VALIDITY_DEAD_TTL_HOURS)).isoformat()
        ok_cutoff = (now - timedelta(hours=Config.VALIDITY_OK_TTL_HOURS)).isoformat()
        rows = self._conn.execute("""
            SELECT url, password, disk_type, validity, alive, fail_cnt, checked_at
            FROM link_index
            WHERE disk_type NOT IN ('magnet', 'ed2k')
              AND (
                validity = 'suspect'
                OR checked_at IS NULL
                OR (validity = 'dead' AND checked_at < ?)
                OR (validity = 'ok' AND checked_at < ?)
                OR (validity = '' AND alive = 1 AND checked_at < ?)
                OR (validity = '' AND alive = 0)
              )
            ORDER BY CASE validity
                        WHEN 'suspect' THEN 0
                        WHEN '' THEN 1
                        WHEN 'dead' THEN 2
                        ELSE 3
                     END,
                     checked_at ASC NULLS FIRST
            LIMIT ?
        """, (dead_cutoff, ok_cutoff, ok_cutoff, limit)).fetchall()
        return [dict(r) for r in rows]

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

    def admin_list(self, keyword: str = "", disk_type: str = "",
                   include_dead: bool = True, page: int = 1,
                   page_size: int = 50) -> dict:
        """后台资源列表（分页 + 关键词/网盘筛选）。只读。"""
        try:
            page = max(1, int(page))
        except (TypeError, ValueError):
            page = 1
        try:
            page_size = min(100, max(1, int(page_size)))
        except (TypeError, ValueError):
            page_size = 50

        where, params = [], []
        keyword = (keyword or "").strip()
        if keyword:
            where.append("(title LIKE ? OR url LIKE ?)")
            params.extend([f"%{keyword}%", f"%{keyword}%"])
        disk_type = (disk_type or "").strip()
        if disk_type:
            where.append("disk_type = ?")
            params.append(disk_type)
        if not include_dead:
            where.append("alive = 1")
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""

        conn = self._conn
        total = conn.execute(
            f"SELECT COUNT(*) FROM link_index{where_sql}", params
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT id, url, password, title, disk_type, source, first_seen, "
            "last_seen, checked_at, alive, validity, state_summary, search_cnt "
            f"FROM link_index{where_sql} ORDER BY last_seen DESC LIMIT ? OFFSET ?",
            params + [page_size, (page - 1) * page_size],
        ).fetchall()
        return {
            "items": [dict(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": max(1, (total + page_size - 1) // page_size),
        }

    def admin_delete_urls(self, urls: list) -> int:
        """后台批量删除索引行（按 URL）。返回删除条数。"""
        urls = [str(u).strip() for u in (urls or []) if str(u).strip()]
        if not urls:
            return 0
        conn = self._conn
        deleted = 0
        for i in range(0, len(urls), 500):
            chunk = urls[i:i + 500]
            placeholders = ",".join(["?" for _ in chunk])
            cur = conn.execute(
                f"DELETE FROM link_index WHERE url IN ({placeholders})", chunk
            )
            deleted += cur.rowcount
        conn.commit()
        return deleted

    def get_recent_titles(self, limit: int = 80) -> list:
        """最近入库的去重资源标题（首页“本地”行数据源）。

        只读查询，不新增表；按 last_seen 倒序取候选，调用方再做降噪。
        """
        try:
            limit = max(1, int(limit))
        except (TypeError, ValueError):
            limit = 80
        rows = self._conn.execute(
            "SELECT title, MAX(last_seen) AS seen FROM link_index "
            "WHERE title != '' AND alive = 1 "
            "GROUP BY title ORDER BY seen DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [r["title"] for r in rows if r["title"]]

    def get_random_titles(self, limit: int = 80) -> list:
        """随机抽样的资源标题（首页“本地”行数据源）。

        首页不能按 last_seen 取“最近入库”：实时搜索会把结果写进索引，
        按时间排序等于把最近被搜过的内容摆到公开首页。这里改为随机抽样。
        """
        try:
            limit = max(1, int(limit))
        except (TypeError, ValueError):
            limit = 80
        rows = self._conn.execute(
            "SELECT title FROM link_index "
            "WHERE title != '' AND alive = 1 "
            "ORDER BY RANDOM() LIMIT ?",
            (limit,),
        ).fetchall()
        return [r["title"] for r in rows if r["title"]]

    # ==========================================
    # 点击统计（热度反哺排序）
    # ==========================================

    def record_click(self, url: str, kw: str = "", source: str = ""):
        """
        记录一次结果点击：
          * click_stats 保留 (url, kw) 全量计数，供热度和排序使用；
          * source_daily_stats 按天/来源/关键词累计，供 CTR 与来源质量表使用。
        source 为空时回退到 link_index 反查（兼容旧前端）。
        """
        url = (url or "").strip()
        if not url:
            return
        kw = (kw or "").strip().lower()
        source = (source or "").strip()
        if not source:
            try:
                row = self._conn.execute(
                    "SELECT source FROM link_index WHERE url = ?", (url,)
                ).fetchone()
                source = ((row["source"] if row else "") or "").strip()
            except sqlite3.Error:
                source = ""
        now = datetime.now().isoformat()
        day = today_str()
        category = classify_category(kw)
        try:
            self._conn.execute("""
                INSERT INTO click_stats (url, kw, source, cnt, last_at)
                VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(url, kw) DO UPDATE SET
                    cnt = cnt + 1,
                    last_at = ?,
                    source = CASE WHEN excluded.source != ''
                                  THEN excluded.source ELSE source END
            """, (url, kw, source, now, now))
            self._conn.execute("""
                INSERT INTO source_daily_stats
                    (day, category, source, kw, impressions, clicks)
                VALUES (?, ?, ?, ?, 0, 1)
                ON CONFLICT(day, source, kw) DO UPDATE SET
                    clicks = clicks + 1,
                    category = CASE WHEN source_daily_stats.category = ''
                                    THEN excluded.category
                                    ELSE source_daily_stats.category END
            """, (day, category, source, kw))
            self._conn.commit()
        except sqlite3.Error as e:
            # 统计失败不能影响点击跳转
            logger.warning(f"记录点击统计失败: {e}")

    def record_impressions(self, source_counts: dict, kw: str = ""):
        """
        记录一次结果页曝光：{source: 卡片数}。

        “按卡片计”而不是按链接计：同一资源卡内的多网盘变体只算一次，
        来源取主卡 source（没有则取第一个变体）。
        """
        if not source_counts:
            return
        kw = (kw or "").strip().lower()
        category = classify_category(kw)
        day = today_str()
        rows = []
        for source, count in (source_counts or {}).items():
            try:
                count = int(count)
            except (TypeError, ValueError):
                continue
            if count <= 0:
                continue
            rows.append((day, category, (source or "").strip(), kw, count))
        if not rows:
            return
        try:
            self._conn.executemany("""
                INSERT INTO source_daily_stats
                    (day, category, source, kw, impressions, clicks)
                VALUES (?, ?, ?, ?, ?, 0)
                ON CONFLICT(day, source, kw) DO UPDATE SET
                    impressions = impressions + excluded.impressions,
                    category = CASE WHEN source_daily_stats.category = ''
                                    THEN excluded.category
                                    ELSE source_daily_stats.category END
            """, rows)
            self._conn.commit()
        except sqlite3.Error as e:
            logger.warning(f"记录来源曝光失败: {e}")

    def get_source_daily_stats(self, days: int = 30, category: str = "all") -> dict:
        """
        按来源汇总窗口内的曝光/点击。

        days=0 表示全部时间；category='all' 表示不按查询类型过滤。
        返回 {"by_source": {source: {"impressions", "clicks"}}, "since": 最早日期}
        """
        sql = ("SELECT source, SUM(impressions) AS impressions, "
               "SUM(clicks) AS clicks FROM source_daily_stats WHERE 1=1")
        params = []
        if days and days > 0:
            cutoff = (date.today() - timedelta(days=days - 1)).isoformat()
            sql += " AND day >= ?"
            params.append(cutoff)
        if category and category != "all":
            sql += " AND category = ?"
            params.append(category)
        sql += " GROUP BY source"
        rows = self._conn.execute(sql, params).fetchall()
        by_source = {
            (r["source"] or ""): {
                "impressions": int(r["impressions"] or 0),
                "clicks": int(r["clicks"] or 0),
            }
            for r in rows
        }
        since = self._conn.execute(
            "SELECT MIN(day) FROM source_daily_stats"
        ).fetchone()[0]
        return {"by_source": by_source, "since": since}

    def get_all_time_clicks_by_source(self) -> dict:
        """全量点击按来源汇总；旧行 source 为空时回退到 link_index。"""
        rows = self._conn.execute("""
            SELECT COALESCE(NULLIF(cs.source, ''), li.source, '') AS click_source,
                   SUM(cs.cnt) AS clicks
            FROM click_stats cs
            LEFT JOIN link_index li ON li.url = cs.url
            GROUP BY click_source
        """).fetchall()
        return {(r["click_source"] or ""): int(r["clicks"] or 0) for r in rows}


    def get_click_stats(self, keyword: str = None) -> dict:
        """
        查询点击统计，供排序加权。

        返回 {url: {"kw": 该关键词下点击数, "total": 全局点击数}}。
        """
        out = {}
        rows = self._conn.execute(
            "SELECT url, SUM(cnt) AS c FROM click_stats GROUP BY url"
        ).fetchall()
        for r in rows:
            out[r["url"]] = {"kw": 0, "total": r["c"] or 0}

        kw = (keyword or "").strip().lower()
        if kw:
            rows = self._conn.execute(
                "SELECT url, cnt FROM click_stats WHERE kw = ?", (kw,)
            ).fetchall()
            for r in rows:
                if r["url"] in out:
                    out[r["url"]]["kw"] = r["cnt"]
                else:
                    out[r["url"]] = {"kw": r["cnt"], "total": r["cnt"]}
        return out

    def clear_click_heat(self, url: str) -> int:
        """撤销某链接的全部点击热度（确认失效时调用，防止死链靠点击置顶）"""
        cur = self._conn.execute(
            "DELETE FROM click_stats WHERE url = ?", (url,)
        )
        self._conn.commit()
        return cur.rowcount

    # ==========================================
    # 零结果守望队列
    # 「今天搜不到的，系统替你守着」——实时+历史全空的词入队，
    # harvester 每轮收割时自动重试全源，命中即出队入库。
    # ==========================================

    def record_zero_result(self, keyword: str):
        """记录一次零结果搜索（UPSERT 累计等待次数）"""
        kw = (keyword or "").strip()
        if not kw:
            return
        now = datetime.now().isoformat()
        self._conn.execute("""
            INSERT INTO zero_result_watch (kw, cnt, last_at)
            VALUES (?, 1, ?)
            ON CONFLICT(kw) DO UPDATE SET
                cnt = cnt + 1,
                last_at = ?
        """, (kw, now, now))
        self._conn.commit()

    def get_zero_result_keywords(self, limit: int = 8) -> list:
        """守望队列：最久未命中的词优先重试"""
        rows = self._conn.execute(
            "SELECT kw FROM zero_result_watch ORDER BY last_at ASC LIMIT ?",
            (limit,)
        ).fetchall()
        return [r["kw"] for r in rows]

    def resolve_zero_result(self, keyword: str) -> int:
        """守望命中（搜到结果了），出队"""
        cur = self._conn.execute(
            "DELETE FROM zero_result_watch WHERE kw = ?", (keyword,)
        )
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
        """
        获取索引库统计（只认 validity 四态）。

        死链率的分母是“已检测”，不是全库；未检测单独列出，
        避免把默认 alive=1 的未体检链接算成“健康”。
        """
        conn = self._conn
        row = conn.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN validity = 'ok' THEN 1 ELSE 0 END) AS ok,
                SUM(CASE WHEN validity = 'suspect' THEN 1 ELSE 0 END) AS suspect,
                SUM(CASE WHEN validity = 'dead' THEN 1 ELSE 0 END) AS dead,
                SUM(CASE WHEN validity IN ('ok', 'suspect', 'dead')
                         THEN 1 ELSE 0 END) AS checked,
                SUM(CASE WHEN validity IS NULL OR validity = ''
                         THEN 1 ELSE 0 END) AS untested
            FROM link_index
        """).fetchone()
        total = int(row["total"] or 0)
        ok = int(row["ok"] or 0)
        suspect = int(row["suspect"] or 0)
        dead = int(row["dead"] or 0)
        checked = int(row["checked"] or 0)
        untested = int(row["untested"] or 0)

        def pct(n, d):
            return round(n / d * 100, 1) if d else 0.0

        # 按网盘类型统计
        type_rows = conn.execute(
            "SELECT disk_type, COUNT(*) as cnt FROM link_index GROUP BY disk_type ORDER BY cnt DESC"
        ).fetchall()
        by_type = {r["disk_type"]: r["cnt"] for r in type_rows}

        # 搜索热词榜（按 search_cnt 排；取 60 条供 /api/hot 降噪后仍有足量）
        hot_rows = conn.execute(
            "SELECT title, search_cnt FROM link_index WHERE title != '' ORDER BY search_cnt DESC LIMIT 60"
        ).fetchall()
        hot_keywords = [{"title": r["title"], "count": r["search_cnt"]} for r in hot_rows]

        return {
            "total": total,
            "checked": checked,
            "ok": ok,
            "suspect": suspect,
            "dead": dead,
            "untested": untested,
            "checked_rate": pct(checked, total),
            "ok_rate": pct(ok, checked),
            "suspect_rate": pct(suspect, checked),
            "dead_rate": pct(dead, checked),
            "by_type": by_type,
            "hot_keywords": hot_keywords,
        }

    # ==========================================
    # 封面海报缓存（豆瓣匹配结果持久化）
    # ==========================================

    def get_posters(self, keys: list) -> dict:
        """
        批量读封面缓存：{key: img 或 ''}。
        成功结果永久有效；"无匹配"（img=''）超过 POSTER_EMPTY_TTL_HOURS 后
        视为过期不再返回（下次重新查询，避免豆瓣限流期的空结果永久粘连）。
        """
        out = {}
        if not keys:
            return out
        cutoff = (datetime.now() - timedelta(
            hours=Config.POSTER_EMPTY_TTL_HOURS)).isoformat()
        conn = self._conn
        for i in range(0, len(keys), 200):
            chunk = [k for k in keys[i:i + 200] if k]
            if not chunk:
                continue
            ph = ",".join(["?" for _ in chunk])
            rows = conn.execute(
                f"SELECT key, img FROM poster_cache "
                f"WHERE key IN ({ph}) AND (img != '' OR updated_at > ?)",
                chunk + [cutoff]
            ).fetchall()
            for r in rows:
                out[r["key"]] = r["img"] or ""
        return out

    def save_poster(self, key: str, img: str):
        """写入封面缓存（img 可为空串=无匹配）"""
        key = (key or "").strip()
        if not key:
            return
        self._conn.execute("""
            INSERT INTO poster_cache (key, img, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                img = excluded.img,
                updated_at = excluded.updated_at
        """, (key, img or "", datetime.now().isoformat()))
        self._conn.commit()

    def close(self):
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None