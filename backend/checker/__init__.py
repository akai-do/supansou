"""
checker —— 智能链接有效性检测引擎

升级自旧版"即时链接体检"（只在点击时查单条）。现在是一套完整的状态机：

  检测源（PanSou /api/check/links）返回四种状态：
    ok          → 有效
    bad         → 失败（连续 DEAD_CONFIRM_STREAK 次才确认失效，防抖动误杀）
    uncertain   → 检测源要求验证，按"疑似失效"降权处理
    unsupported → 磁力/ed2k 等无法检测，保持未检测态，永不降权

  有效性四态（存 SQLite link_index.validity）：
    ok / suspect / dead / ''（未检测）

  新鲜度 TTL（避免重复请求检测源）：
    ok      72h 内视为可信
    suspect 30min 后复检（尽快确认或洗白）
    dead    12h 后自动复查（探测分享恢复）

  三个检测入口共用同一状态机：
    1. 周期巡检（后台线程，按优先级取候选）
    2. 搜索后的静默补检（api 层触发）
    3. 前端当前页批量检测（POST /api/check/batch）
"""
import time
import logging
import threading
from datetime import datetime, timedelta
from typing import Callable, Optional
from config import Config

logger = logging.getLogger("checker")

# 检测源支持的网盘类型之外的类型不送检
_UNCHECKABLE_TYPES = {"magnet", "ed2k", ""}


def is_checkable(disk_type: str) -> bool:
    """该网盘类型是否能被检测源检测"""
    return str(disk_type or "").strip().lower() not in _UNCHECKABLE_TYPES


class LinkChecker:
    """智能链接有效性检测器（周期巡检 + 按需批量检测）"""

    def __init__(self, index_db, check_func: Callable,
                 interval_hours: int = None, batch_size: int = None,
                 cycle_limit: int = None):
        """
        参数:
            index_db: IndexDatabase 实例
            check_func: 检测函数，接收 [(url, disk_type, password), ...]
                        返回 [{"url": "...", "state": "ok|bad|uncertain|unsupported",
                               "summary": "..."}, ...]
            interval_hours: 巡检周期（小时）
            batch_size: 每批送检数量
            cycle_limit: 每轮巡检最多检测条数
        """
        self.index_db = index_db
        self.check_func = check_func
        self.interval = (interval_hours or Config.CHECKER_INTERVAL_HOURS) * 3600
        self.batch_size = batch_size or Config.CHECKER_BATCH_SIZE
        self.cycle_limit = cycle_limit or Config.CHECKER_CYCLE_LIMIT
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.stats = {
            "total_checks": 0,
            "alive_count": 0,
            "dead_count": 0,
            "suspect_count": 0,
            "last_check_time": None,
            "errors": [],
        }

    # ---------- 新鲜度 ----------

    @staticmethod
    def _is_fresh(vinfo: dict) -> bool:
        """按当前有效性状态判断检测结果是否仍在保鲜期内"""
        v = vinfo.get("validity") or ""
        checked = vinfo.get("checked_at") or ""
        if not checked:
            return False
        try:
            age = datetime.now() - datetime.fromisoformat(checked)
        except (ValueError, TypeError):
            return False
        if v == "ok":
            return age < timedelta(hours=Config.VALIDITY_OK_TTL_HOURS)
        if v == "suspect":
            return age < timedelta(minutes=Config.VALIDITY_SUSPECT_TTL_MINUTES)
        if v == "dead":
            return age < timedelta(hours=Config.VALIDITY_DEAD_TTL_HOURS)
        return False

    # ---------- 核心：批量智能检测 ----------

    def check_items(self, items: list, force: bool = False) -> dict:
        """
        批量检测一组链接，写入状态机并返回各链接的当前有效性。

        参数:
            items: [{"url", "disk_type", "password"}]
            force: True 跳过新鲜度缓存强制送检
        返回:
            {url: {"validity", "state", "summary", "checked_at", "fresh"}}
              fresh=True 表示复用保鲜期内的既有结果，未真正送检
        """
        items = [it for it in items if it and it.get("url")]
        if not items:
            return {}

        out = {}
        pending = []

        # 1. 无法检测的类型直接回填现状（磁力等永不降权）
        # 2. 保鲜期内且未强制 → 复用既有结果
        vmap = self.index_db.get_validity_map([it["url"] for it in items])
        for it in items:
            url = it["url"]
            vinfo = vmap.get(url) or {
                "validity": "", "state_summary": "", "checked_at": "",
            }
            entry = {
                "validity": vinfo.get("validity") or "",
                "state": "cached" if vinfo.get("validity") else "unknown",
                "summary": vinfo.get("state_summary") or "",
                "checked_at": vinfo.get("checked_at") or "",
                "fresh": False,
            }
            if not is_checkable(it.get("disk_type")):
                entry["state"] = "unsupported"
            elif not force and self._is_fresh(vinfo):
                entry["state"] = {
                    "ok": "ok", "suspect": "uncertain", "dead": "bad",
                }.get(entry["validity"], "cached")
                entry["fresh"] = True
            else:
                pending.append(it)
            out[url] = entry

        if not pending:
            return out

        # 3. 分批送检（检测源单批容量有限）
        for i in range(0, len(pending), self.batch_size):
            if self._stop_event.is_set():
                break
            batch = pending[i:i + self.batch_size]
            check_input = [
                (it["url"], it.get("disk_type") or "", it.get("password") or "")
                for it in batch
            ]
            try:
                results = self.check_func(check_input)
            except Exception as e:
                logger.error(f"[智能检测] 批次送检失败: {e}")
                self.stats["errors"] = (self.stats["errors"] + [str(e)])[-10:]
                continue

            for r in results or []:
                url = r.get("url")
                state = str(r.get("state") or "").lower()
                summary = r.get("summary") or ""
                if not url or url not in out:
                    continue
                # 归一化：检测源可能返回别名状态
                if state not in ("ok", "bad", "uncertain", "unsupported"):
                    state = "uncertain" if state else "unsupported"
                applied = self.index_db.apply_check_result(url, state, summary)
                validity = applied.get("validity")
                if validity is None:
                    # URL 不在索引库（如点击的实时新链）→ 只回报不落库
                    out[url].update({
                        "state": state, "summary": summary,
                        "validity": "ok" if state == "ok"
                                    else ("dead" if state == "bad" else ""),
                    })
                else:
                    out[url].update({
                        "state": state, "validity": validity,
                        "summary": summary, "checked_at": datetime.now().isoformat(),
                    })
                self.stats["total_checks"] += 1
                if state == "ok":
                    self.stats["alive_count"] += 1
                elif state == "bad":
                    if out[url].get("validity") == "dead":
                        self.stats["dead_count"] += 1
                    else:
                        self.stats["suspect_count"] += 1

            if i + self.batch_size < len(pending):
                time.sleep(0.3)  # 批间小歇，避免打爆检测源

        return out

    # ---------- 周期巡检 ----------

    def start(self):
        """启动巡检器（后台线程）"""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info(f"[智能检测] 已启动，周期: {self.interval//3600}h/轮, "
                    f"每轮上限 {self.cycle_limit} 条")

    def stop(self):
        """停止巡检器"""
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        logger.info(f"[智能检测] 已停止")

    def _loop(self):
        """巡检主循环"""
        self._check_once()
        while not self._stop_event.is_set():
            if self._stop_event.wait(self.interval):
                break
            self._check_once()

    def _check_once(self):
        """执行一轮巡检：按优先级取候选，分批送检"""
        logger.info("[智能检测] 开始一轮巡检...")

        links = self.index_db.get_smart_check_candidates(limit=self.cycle_limit)
        if not links:
            logger.info("[智能检测] 没有需要检测的链接")
            return

        logger.info(f"[智能检测] 待检测: {len(links)} 条链接")

        items = [
            {"url": l["url"], "disk_type": l.get("disk_type") or "",
             "password": l.get("password") or ""}
            for l in links
        ]
        result = self.check_items(items)
        ok = sum(1 for v in result.values() if v.get("validity") == "ok")
        dead = sum(1 for v in result.values() if v.get("validity") == "dead")
        suspect = sum(1 for v in result.values() if v.get("validity") == "suspect")
        self.stats["last_check_time"] = datetime.now().isoformat()
        logger.info(f"[智能检测] 完成: 有效 {ok}, 疑似 {suspect}, 确认失效 {dead}")

    def check_now(self):
        """立即执行一轮巡检（手动触发，同步）"""
        self._check_once()

    def get_stats(self) -> dict:
        return dict(self.stats)

    def is_running(self) -> bool:
        return self._running
