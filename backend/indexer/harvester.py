"""
harvester —— 定时收割器

当年度盘搜爬虫的"现代轻量替代"：
你不爬百度网盘页面，而是用 PanSou 的搜索能力，定时用预设关键词
主动搜索并收割结果进入索引库。

搜得越多，索引越强 —— 实现"自我增长的搜索引擎"。
"""
import time
import logging
import threading
from typing import Callable, Optional
from config import Config

logger = logging.getLogger("harvester")


class Harvester:
    """定时收割器 —— 用 PanSou 搜预设关键词，将结果存入索引"""

    def __init__(self, index_db, search_func: Callable, keywords: list = None,
                 interval_hours: int = None):
        """
        参数:
            index_db: IndexDatabase 实例
            search_func: 搜索函数，接收 keyword 参数，返回 link 列表
            keywords: 要收割的关键词列表
            interval_hours: 收割周期（小时）
        """
        self.index_db = index_db
        self.search_func = search_func
        self.keywords = keywords or Config.HARVEST_KEYWORDS
        self.interval = (interval_hours or Config.HARVEST_INTERVAL_HOURS) * 3600
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.total_harvested = 0

    def start(self):
        """启动收割器（后台线程）"""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info(f"[Harvester] 已启动，周期: {self.interval//3600}h，"
                     f"关键词: {len(self.keywords)} 个")

    def stop(self):
        """停止收割器"""
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        logger.info(f"[Harvester] 已停止，累计收割: {self.total_harvested} 条")

    def _loop(self):
        """收割主循环"""
        # 启动后立即收割一轮
        self._harvest_cycle()

        while not self._stop_event.is_set():
            if self._stop_event.wait(self.interval):
                break
            self._harvest_cycle()

    def _harvest_cycle(self):
        """执行一轮收割"""
        logger.info(f"[Harvester] 开始一轮收割 ({len(self.keywords)} 个关键词)...")

        for i, kw in enumerate(self.keywords):
            kw = kw.strip()
            if not kw:
                continue

            try:
                links = self.search_func(kw)
                if links:
                    count = self.index_db.store_links(links)
                    self.total_harvested += count
                    logger.info(f"  [{i+1}/{len(self.keywords)}] '{kw}' → "
                                f"收割 {count} 条 (累计 {self.total_harvested})")
            except Exception as e:
                logger.error(f"  [{i+1}/{len(self.keywords)}] '{kw}' 失败: {e}")

            # 每搜一个关键词间隔 2 秒
            if i < len(self.keywords) - 1:
                time.sleep(2)

        logger.info(f"[Harvester] 本轮收割完成，累计: {self.total_harvested} 条")

    def get_stats(self) -> dict:
        return {
            "running": self._running,
            "keywords": len(self.keywords),
            "interval_hours": self.interval // 3600,
            "total_harvested": self.total_harvested,
        }