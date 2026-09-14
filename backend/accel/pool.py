"""多账号池：账号级限速的唯一根治手段。

百度对 pcs 直链的限速是**按账号**计的：同一 BDUSS 下，无论开多少连接、
多少个不同直链，总吞吐会撞到同一个天花板。所以当单账号已经封顶
（调度器 verdict=account_cap）时，继续加连接毫无收益，只能：

  A. 多个账号**分文件**并行 —— 文件 1 走账号 A，文件 2 走账号 B，带宽叠加；
  B. 同一文件**分段跨账号** —— 切成 K 段分给 K 个账号，单文件的带宽也叠加
     （见 segments.py）。

本模块只管账号的注册、健康度与调度，不碰分段逻辑。

约定：
- `account_id = 0` 是"主账号"，即 settings 表里的 BDUSS，不落在 accounts 表。
- 账号被百度风控（errno 105/-105/-62/-70/132）时进入冷却，冷却期内不再被选中。
- BDUSS 明文只存本地 sqlite；对外一律走 mask_secret()。
"""
from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger("accel")

# 触发冷却的 errno（风控/账号级失败）
COOLDOWN_ERRNOS = {105, -105, -62, -70, -19, 132, 8001}
COOLDOWN_SECONDS = 600
PRIMARY_ID = 0


def _i(v, d=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return d


class AccountPool:
    def __init__(self, store, client_factory, primary_provider):
        """client_factory(bduss, stoken) → BaiduShareClient
        primary_provider() → (bduss, stoken)  主账号凭证（settings 表）"""
        self.store = store
        self.client_factory = client_factory
        self.primary_provider = primary_provider
        self._lock = threading.RLock()
        self._rr = {}          # account_id → 最近使用时间（轮转用）
        self._rr_seq = 0

    # ---------- 账号列表 ----------
    def rows(self) -> list:
        return self.store.list_accounts()

    def enabled_rows(self) -> list:
        now = time.time()
        return [r for r in self.rows()
                if r["enabled"] and _i(r["cooldown_until"]) <= now]

    def count_enabled(self) -> int:
        """可用于叠加带宽的账号数（含主账号，前提是主账号已配置）。"""
        n = len(self.enabled_rows())
        bduss, _ = self.primary_provider()
        if bduss:
            n += 1
        return n

    def public_list(self) -> list:
        from .store import mask_secret
        out = []
        bduss, stoken = self.primary_provider()
        if bduss:
            out.append({
                "id": PRIMARY_ID, "label": "主账号（设置里的 BDUSS）",
                "hint": mask_secret(bduss), "enabled": True,
                "primary": True, "cooldown_left": 0,
                "ok_count": 0, "fail_count": 0,
            })
        now = time.time()
        for r in self.rows():
            out.append({
                "id": r["id"], "label": r["label"] or f"账号 #{r['id']}",
                "hint": mask_secret(r["bduss"]), "enabled": bool(r["enabled"]),
                "primary": False,
                "cooldown_left": max(0, int(_i(r["cooldown_until"]) - now)),
                "ok_count": _i(r["ok_count"]), "fail_count": _i(r["fail_count"]),
                "has_stoken": bool(r["stoken"]),
            })
        return out

    # ---------- 选择 ----------
    def pick(self, n: int = 1, exclude: set | None = None) -> list:
        """挑 n 个可用账号（轮转 + 失败率加权），返回 [{id,label,bduss,stoken}]。

        账号不足时返回实际数量（可能少于 n），调用方需自行降级。
        """
        exclude = set(exclude or ())
        with self._lock:
            cand = []
            bduss, stoken = self.primary_provider()
            if bduss and PRIMARY_ID not in exclude:
                cand.append({"id": PRIMARY_ID, "label": "主账号",
                             "bduss": bduss, "stoken": stoken, "weight": 3})
            for r in self.enabled_rows():
                if r["id"] in exclude:
                    continue
                ok = _i(r["ok_count"])
                bad = _i(r["fail_count"])
                cand.append({"id": r["id"], "label": r["label"] or f"#{r['id']}",
                             "bduss": r["bduss"], "stoken": r["stoken"],
                             "weight": max(1, 3 + ok - 2 * bad)})
            if not cand:
                return []
            # 加权轮转：权重高 + 最久没用的排前面
            now = time.time()
            cand.sort(key=lambda c: (
                -(c["weight"] * 10 - (now - self._rr.get(c["id"], 0)) / 60.0)))
            out = cand[:max(1, n)]
            self._rr_seq += 1
            for c in out:
                self._rr[c["id"]] = now
            return out

    def any_client(self, exclude: set | None = None):
        """取一个可用账号的 client；主账号优先（已配置时）。"""
        picked = self.pick(1, exclude=exclude)
        if not picked:
            return None, None
        a = picked[0]
        return a["id"], self.client_factory(a["bduss"], a["stoken"])

    # ---------- 健康度回报 ----------
    def report(self, account_id: int, ok: bool, errno=None, note: str = ""):
        if account_id == PRIMARY_ID:
            if not ok and errno in COOLDOWN_ERRNOS:
                logger.warning("[加速] 主账号触发风控（errno=%s），建议换账号或稍后再试：%s",
                               errno, note)
            return
        with self._lock:
            if ok:
                self.store.bump_account(account_id, ok=True)
            else:
                self.store.bump_account(account_id, ok=False)
                if errno in COOLDOWN_ERRNOS:
                    self.store.set_account_cooldown(
                        account_id, time.time() + COOLDOWN_SECONDS)
                    logger.warning("[加速] 账号 #%s 触发风控（errno=%s），冷却 %ss",
                                   account_id, errno, COOLDOWN_SECONDS)

    # ---------- 增删改 ----------
    def add(self, label: str, bduss: str, stoken: str = "") -> int:
        return self.store.add_account(label, bduss, stoken)

    def remove(self, account_id: int):
        self.store.delete_account(account_id)

    def set_enabled(self, account_id: int, enabled: bool):
        self.store.update_account(account_id, enabled=1 if enabled else 0)

    def clear_cooldown(self, account_id: int):
        self.store.set_account_cooldown(account_id, 0)
