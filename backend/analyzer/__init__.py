"""
analyzer —— 搜索分析模块

提供搜索热词排名、来源分布、链接健康趋势等数据分析能力。
所有数据来自本地索引库，不需要外部服务。
"""
from collections import Counter
from datetime import datetime, timedelta
from typing import Optional


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
        """链接健康概览"""
        stats = self.index_db.get_stats()
        total = stats.get("total", 0)
        alive = stats.get("alive", 0)
        dead = stats.get("dead", 0)

        return {
            "total_links": total,
            "alive_links": alive,
            "dead_links": dead,
            "alive_rate": round(alive / total * 100, 1) if total > 0 else 0,
            "dead_rate": round(dead / total * 100, 1) if total > 0 else 0,
        }

    def get_source_distribution(self) -> list:
        """数据来源分布（TG频道 vs 插件 vs 收割器）"""
        conn = self.index_db._conn
        rows = conn.execute("""
            SELECT
                CASE
                    WHEN source LIKE 'tg:%' THEN 'Telegram 频道'
                    WHEN source LIKE 'plugin:%' THEN '搜索插件'
                    ELSE '定时收割'
                END as source_group,
                COUNT(*) as count,
                SUM(CASE WHEN alive=1 THEN 1 ELSE 0 END) as alive_count
            FROM link_index
            GROUP BY source_group
            ORDER BY count DESC
        """).fetchall()
        return [dict(r) for r in rows]

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