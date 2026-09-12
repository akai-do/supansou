"""
checker —— 链接巡检模块

当年度盘搜的最大痛点：历史链接大量失效。
Checker 周期性地从索引库中取出"超过 N 天未检测的链接"，
调用 PanSou 的 check_service 进行批量存活检测，
存活状态写回索引库，用户搜索时优先展示有效链接。
"""
import time
import logging
import threading
from datetime import datetime, timedelta
from typing import Callable, Optional
from config import Config

logger = logging.getLogger("checker")


class LinkChecker:
    """链接存活巡检器"""

    def __init__(self, index_db, check_func: Callable,
                 interval_hours: int = None, batch_size: int = None):
        """
        参数:
            index_db: IndexDatabase 实例
            check_func: 检测函数，接收 [(url, disk_type, password), ...]
                        返回 [{"url": "...", "alive": True/False}, ...]
            interval_hours: 巡检周期（小时）
            batch_size: 每批检测数量
        """
        self.index_db = index_db
        self.check_func = check_func
        self.interval = (interval_hours or Config.CHECKER_INTERVAL_HOURS) * 3600
        self.batch_size = batch_size or Config.CHECKER_BATCH_SIZE
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.stats = {
            "total_checks": 0,
            "alive_count": 0,
            "dead_count": 0,
            "last_check_time": None,
            "errors": [],
        }

    def start(self):
        """启动巡检器（后台线程）"""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info(f"[Checker] 已启动，周期: {self.interval//3600}h")

    def stop(self):
        """停止巡检器"""
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        logger.info(f"[Checker] 已停止")

    def _loop(self):
        """巡检主循环"""
        self._check_once()
        while not self._stop_event.is_set():
            if self._stop_event.wait(self.interval):
                break
            self._check_once()

    def _check_once(self):
        """执行一轮巡检"""
        logger.info("[Checker] 开始一轮链接巡检...")

        # 从索引库取超过 7 天未检测的链接
        links = self.index_db.get_unchecked_links(days=7, limit=200)
        if not links:
            logger.info("[Checker] 没有需要检测的链接")
            return

        logger.info(f"[Checker] 待检测: {len(links)} 条链接")

        # 分批检测
        alive = 0
        dead = 0
        errors = []

        for i in range(0, len(links), self.batch_size):
            batch = links[i:i + self.batch_size]
            check_input = [(l["url"], l["disk_type"], l.get("password", ""))
                          for l in batch]

            try:
                results = self.check_func(check_input)
                for r in results:
                    self.index_db.update_alive_status(
                        r["url"], r["alive"]
                    )
                    if r["alive"]:
                        alive += 1
                    else:
                        dead += 1
            except Exception as e:
                errors.append(str(e))
                logger.error(f"[Checker] 批次检测失败: {e}")

            # 每批间隔 500ms
            if i + self.batch_size < len(links):
                time.sleep(0.5)

        # 更新统计
        self.stats["total_checks"] += len(links)
        self.stats["alive_count"] = alive
        self.stats["dead_count"] = dead
        self.stats["last_check_time"] = datetime.now().isoformat()
        if errors:
            self.stats["errors"] = errors[-10:]  # 只保留最近10条错误

        logger.info(f"[Checker] 完成: {alive} 条存活, {dead} 条失效")

    def check_now(self):
        """立即执行一次巡检（手动触发）"""
        if not self._running:
            self._check_once()

    def get_stats(self) -> dict:
        return self.stats

    def is_running(self) -> bool:
        return self._running