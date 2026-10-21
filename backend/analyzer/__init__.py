"""
analyzer —— 搜索分析模块

提供搜索热词排名、来源质量、链接健康趋势等数据分析能力。
所有数据来自本地索引库，不需要外部服务。

统计口径（2026-10 起统一）：
  * 只认 validity 四态（ok / suspect / dead / 未检测）
  * 死链率 = dead / 已检测，未检测不计入分母
  * 来源质量 = 库存 + 已检测率 + 死链率 + 疑似率 + 独家率 + 曝光/点击
"""
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from config import Config
from source_stats import CATEGORY_LABELS, normalize_title


# 来源质量聚合要扫 62k 行；按 (days, category) 缓存，避免每次打开统计页重算。
_SOURCE_QUALITY_CACHE = {}
_SOURCE_QUALITY_LOCK = threading.Lock()
_SOURCE_PENALTY_CACHE = {"t": 0.0, "map": {}}


def _pct(n: float, d: float) -> Optional[float]:
    """百分比；分母为 0 返回 None（前端显示“—”，不显示误导性的 0%）。"""
    return round(n / d * 100, 1) if d else None


def _parse_dt(value) -> Optional[datetime]:
    """兼容 SQLite CURRENT_TIMESTAMP（空格）与 isoformat（T）两种写法。"""
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00").replace(" ", "T")
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def _suggestion(checked: int, dead_rate: Optional[float],
                ok_rate: Optional[float], exclusive_rate: Optional[float],
                low_sample: bool) -> str:
    if low_sample:
        return "样本不足"
    if dead_rate is not None and dead_rate >= 50:
        if (exclusive_rate or 0) < 20:
            return "建议降权"
        return "观察（独家率尚可）"
    if (ok_rate or 0) >= 70 and (exclusive_rate or 0) >= 30:
        return "优先保留"
    return "观察"


class Analyzer:
    """搜索数据分析器"""

    def __init__(self, index_db):
        self.index_db = index_db

    def get_hot_search_keywords(self, limit: int = 20) -> list:
        """
        搜索热词排行榜

        基于索引库中 search_cnt 最高的标题关键词提取。
        这反映的是"哪些资源被搜到的最多"（不是搜索日志）。
        """
        stats = self.index_db.get_stats()
        return stats.get("hot_keywords", [])[:limit]

    def get_disk_type_distribution(self) -> list:
        """各网盘类型的链接数量分布"""
        stats = self.index_db.get_stats()
        by_type = stats.get("by_type", {})
        total = stats.get("total", 1)
        return sorted([
            {"type": k, "count": v, "percentage": round(v / total * 100, 1)}
            for k, v in by_type.items()
        ], key=lambda x: x["count"], reverse=True)

    def get_link_health_overview(self) -> dict:
        """链接健康概览（validity 口径，未检测单列）"""
        stats = self.index_db.get_stats()
        return {
            "total_links": stats.get("total", 0),
            "checked_links": stats.get("checked", 0),
            "ok_links": stats.get("ok", 0),
            "suspect_links": stats.get("suspect", 0),
            "dead_links": stats.get("dead", 0),
            "untested_links": stats.get("untested", 0),
            "checked_rate": stats.get("checked_rate", 0),
            "ok_rate": stats.get("ok_rate", 0),
            "suspect_rate": stats.get("suspect_rate", 0),
            "dead_rate": stats.get("dead_rate", 0),
        }

    def get_source_distribution(self) -> list:
        """数据来源类型分布（Telegram / 插件 / 收割），只做概览。

        真正的单来源质量表见 get_source_quality()。
        """
        conn = self.index_db._conn
        rows = conn.execute("""
            SELECT
                CASE
                    WHEN source LIKE 'tg:%' THEN 'Telegram 频道'
                    WHEN source LIKE 'plugin:%' THEN '搜索插件'
                    ELSE '定时收割'
                END AS source_group,
                COUNT(*) AS total,
                SUM(CASE WHEN validity IN ('ok', 'suspect', 'dead')
                         THEN 1 ELSE 0 END) AS checked,
                SUM(CASE WHEN validity = 'dead' THEN 1 ELSE 0 END) AS dead
            FROM link_index
            GROUP BY source_group
            ORDER BY total DESC
        """).fetchall()
        out = []
        for r in rows:
            checked = int(r["checked"] or 0)
            dead = int(r["dead"] or 0)
            out.append({
                "source_group": r["source_group"],
                "count": int(r["total"] or 0),
                "checked": checked,
                "dead": dead,
                "dead_rate": _pct(dead, checked),
            })
        return out

    def get_index_growth(self, days: int = 30) -> list:
        """
        索引库增长趋势

        按天统计新增链接数。
        如果数据不足 days 天，以实际数据为准。
        """
        conn = self.index_db._conn
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

        rows = conn.execute("""
            SELECT date(first_seen) as day, COUNT(*) as count
            FROM link_index
            WHERE first_seen >= ?
            GROUP BY day
            ORDER BY day ASC
        """, (cutoff,)).fetchall()

        return [{"date": r["day"], "new_links": r["count"]} for r in rows]

    def get_source_quality(self, days: int = 30, category: str = "all",
                           force: bool = False) -> dict:
        """
        单来源质量表。

        参数:
            days: 统计窗口天数；0 表示全部时间
            category: all/movie/study/software/music（按标题/关键词启发式归类）
            force: True 时跳过缓存（统计页“刷新”按钮用）

        返回:
            {
              generated_at, days, category, category_label, stats_since,
              low_sample_threshold, note,
              totals: {...},
              sources: [
                {source, total, checked, checked_rate, ok, ok_rate,
                 suspect, suspect_rate, dead, dead_rate,
                 new_7d, new_30d, last_checked,
                 impressions, clicks, ctr,
                 all_time_clicks, click_share, clicks_per_100,
                 distinct_titles, unique_titles, exclusive_rate,
                 quality_score, suggestion, low_sample}
              ]
            }
        """
        try:
            days = int(days)
        except (TypeError, ValueError):
            days = 30
        days = max(0, min(365, days))
        if category not in CATEGORY_LABELS:
            category = "all"

        cache_key = (days, category)
        now_ts = time.time()
        with _SOURCE_QUALITY_LOCK:
            cached = _SOURCE_QUALITY_CACHE.get(cache_key)
            if (not force and cached
                    and now_ts - cached[0] < Config.SOURCE_STATS_CACHE_SECONDS):
                return cached[1]

        now = datetime.now()
        cutoff_7 = now - timedelta(days=7)
        cutoff_30 = now - timedelta(days=30)
        threshold = Config.SOURCE_LOW_SAMPLE_THRESHOLD

        conn = self.index_db._conn
        if category == "all":
            rows = conn.execute("""
                SELECT source, title, validity, first_seen, checked_at
                FROM link_index
            """).fetchall()
        else:
            # 类别在入库时已写入 link_index.category（标题启发式），
            # 这里直接 SQL 过滤，避免每次统计都扫全库重新归类。
            rows = conn.execute("""
                SELECT source, title, validity, first_seen, checked_at
                FROM link_index WHERE category = ?
            """, (category,)).fetchall()

        agg = defaultdict(lambda: {
            "total": 0, "ok": 0, "suspect": 0, "dead": 0,
            "checked": 0, "untested": 0, "new_7d": 0, "new_30d": 0,
            "last_checked": None,
        })
        source_titles = defaultdict(set)
        title_sources = defaultdict(set)

        for r in rows:
            title = r["title"] or ""
            src = (r["source"] or "").strip()
            a = agg[src]
            a["total"] += 1

            v = (r["validity"] or "").strip()
            if v == "ok":
                a["ok"] += 1
                a["checked"] += 1
            elif v == "suspect":
                a["suspect"] += 1
                a["checked"] += 1
            elif v == "dead":
                a["dead"] += 1
                a["checked"] += 1
            else:
                a["untested"] += 1

            fs = _parse_dt(r["first_seen"])
            if fs is not None:
                if fs >= cutoff_7:
                    a["new_7d"] += 1
                if fs >= cutoff_30:
                    a["new_30d"] += 1

            ck = _parse_dt(r["checked_at"])
            if ck is not None and (a["last_checked"] is None or ck > a["last_checked"]):
                a["last_checked"] = ck

            nt = normalize_title(title)
            if nt:
                source_titles[src].add(nt)
                title_sources[nt].add(src)

        daily = self.index_db.get_source_daily_stats(days=days, category=category)
        daily_by_source = daily.get("by_source") or {}
        all_clicks_by_source = self.index_db.get_all_time_clicks_by_source()
        total_all_clicks = sum(all_clicks_by_source.values())
        total_window_clicks = sum(
            int(d.get("clicks") or 0) for d in daily_by_source.values()
        )

        sources = []
        for src, a in agg.items():
            checked = a["checked"]
            total = a["total"]
            dead_rate = _pct(a["dead"], checked)
            suspect_rate = _pct(a["suspect"], checked)
            ok_rate = _pct(a["ok"], checked)
            checked_rate = _pct(checked, total)

            distinct_titles = len(source_titles.get(src) or ())
            unique_titles = sum(
                1 for t in (source_titles.get(src) or ())
                if len(title_sources.get(t) or ()) == 1
            )
            exclusive_rate = _pct(unique_titles, distinct_titles)

            d = daily_by_source.get(src) or {}
            impressions = int(d.get("impressions") or 0)
            clicks = int(d.get("clicks") or 0)
            ctr = round(clicks / impressions * 100, 2) if impressions else None

            all_clicks = int(all_clicks_by_source.get(src) or 0)
            # 全量视图用全量点击；按类别看时用窗口内该类别点击，
            # 避免把“影视点击”混进“学习来源”的每百条点击。
            if category == "all":
                click_basis = "all_time"
                basis_clicks = all_clicks
                basis_total_clicks = total_all_clicks
            else:
                click_basis = "window"
                basis_clicks = clicks
                basis_total_clicks = total_window_clicks
            click_share = _pct(basis_clicks, basis_total_clicks)
            clicks_per_100 = (
                round(basis_clicks / total * 100, 2) if total else None
            )

            low_sample = checked < threshold
            if low_sample:
                quality_score = None
            else:
                usable = (a["ok"] / checked) if checked else 0.0
                exclusive = (unique_titles / distinct_titles) if distinct_titles else 0.0
                click_weight = 0.5
                if impressions:
                    click_weight = min(1.0, (clicks / impressions) / 0.10)
                quality_score = round(
                    usable * (0.5 + 0.5 * exclusive) * (0.5 + 0.5 * click_weight),
                    3,
                )

            sources.append({
                "source": src,
                "total": total,
                "checked": checked,
                "checked_rate": checked_rate,
                "ok": a["ok"],
                "ok_rate": ok_rate,
                "suspect": a["suspect"],
                "suspect_rate": suspect_rate,
                "dead": a["dead"],
                "dead_rate": dead_rate,
                "untested": a["untested"],
                "new_7d": a["new_7d"],
                "new_30d": a["new_30d"],
                "last_checked": (
                    a["last_checked"].isoformat(timespec="seconds")
                    if a["last_checked"] else ""
                ),
                "impressions": impressions,
                "clicks": clicks,
                "ctr": ctr,
                "all_time_clicks": all_clicks,
                "click_basis": click_basis,
                "click_share": click_share,
                "clicks_per_100": clicks_per_100,
                "distinct_titles": distinct_titles,
                "unique_titles": unique_titles,
                "exclusive_rate": exclusive_rate,
                "quality_score": quality_score,
                "suggestion": _suggestion(
                    checked, dead_rate, ok_rate, exclusive_rate, low_sample
                ),
                "low_sample": low_sample,
            })

        sources.sort(key=lambda x: (-x["total"], x["source"]))
        total_links = sum(s["total"] for s in sources)
        total_checked = sum(s["checked"] for s in sources)
        total_ok = sum(s["ok"] for s in sources)
        total_suspect = sum(s["suspect"] for s in sources)
        total_dead = sum(s["dead"] for s in sources)
        total_untested = sum(s["untested"] for s in sources)
        total_impressions = sum(s["impressions"] for s in sources)
        total_clicks = sum(s["clicks"] for s in sources)

        result = {
            "generated_at": now.isoformat(timespec="seconds"),
            "days": days,
            "category": category,
            "category_label": CATEGORY_LABELS.get(category, "全部"),
            "click_basis": "all_time" if category == "all" else "window",
            "stats_since": daily.get("since"),
            "low_sample_threshold": threshold,
            "note": "死链率 = 确认失效 / 已检测；未检测不计入分母。"
                    "曝光/点击按天聚合，升级后开始记录。",
            "totals": {
                "total": total_links,
                "checked": total_checked,
                "ok": total_ok,
                "suspect": total_suspect,
                "dead": total_dead,
                "untested": total_untested,
                "checked_rate": _pct(total_checked, total_links),
                "dead_rate": _pct(total_dead, total_checked),
                "impressions": total_impressions,
                "clicks": total_clicks,
                "all_time_clicks": total_all_clicks,
                "ctr": (round(total_clicks / total_impressions * 100, 2)
                        if total_impressions else None),
            },
            "sources": sources,
        }

        with _SOURCE_QUALITY_LOCK:
            _SOURCE_QUALITY_CACHE[cache_key] = (time.time(), result)
        return result

    def get_source_penalty_map(self) -> dict:
        """
        搜索排序用的来源降权表：{source: 0.85}。

        只对“已检测样本足够 + 死链率 >= 50% + 独家率 < 20%”的来源降权，
        避免把高死链但提供独家资源的来源误杀。缓存 5 分钟。
        """
        now = time.time()
        with _SOURCE_QUALITY_LOCK:
            if now - _SOURCE_PENALTY_CACHE["t"] < 300:
                return dict(_SOURCE_PENALTY_CACHE["map"])

        quality = self.get_source_quality(days=30, category="all")
        penalties = {}
        for src in quality.get("sources") or []:
            checked = int(src.get("checked") or 0)
            dead_rate = src.get("dead_rate")
            exclusive = src.get("exclusive_rate")
            if (checked >= Config.SOURCE_LOW_SAMPLE_THRESHOLD
                    and dead_rate is not None and dead_rate >= 50
                    and exclusive is not None and exclusive < 20):
                penalties[src.get("source") or ""] = 0.85

        with _SOURCE_QUALITY_LOCK:
            _SOURCE_PENALTY_CACHE["t"] = now
            _SOURCE_PENALTY_CACHE["map"] = penalties
        return dict(penalties)
