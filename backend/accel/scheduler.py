"""带宽调度器：解决「慢速任务占满带宽 + 账号级限速」两大实测瓶颈。

问题复现（原版行为）
--------------------
原实现把每个文件都作为独立任务推给 aria2，全局 `max-concurrent-downloads=3`。
于是 3 个"慢任务"（被百度单连接限速、或小文件元数据交换占主导）会长期占住
全部 3 个并发槽与各自的 16 条连接，而用户真正想要的快文件只能排在 waiting 里
干等 —— 表现就是「整体速度上不去，带宽被慢速任务白白吃掉」。

本调度器每 SCHED_TICK 秒做一次闭环控制
--------------------------------------
1. **采样**：tellActive / tellWaiting / getGlobalStat，算每个任务的 EWMA 速度。
2. **并发槽分配**：把活跃数压到 `max_concurrent` 以内，优先保住「跑得动」的任务。
3. **慢任务让路（yield）**：速度 < slow_threshold 且连续 slow_ticks 次命中，
   就 pause 它、把槽让给队列里的任务；被让路任务带 aging 计数，冷却到期后
   一定会被重新放回，保证不会被饿死。
4. **自适应连接爬坡**：对表现好的任务逐步提高 split（16→24→32→48→64），
   速度没有跟着涨就回退 —— 用来找单直链的并发甜点，也是"连接是否封顶"的探针。
5. **归因**：把「触顶本地上限 / 单连接限速 / 账号级限速 / 槽位饥饿」判出来，
   给前端一句话结论和对应动作建议。
"""
from __future__ import annotations

import collections
import logging
import threading
import time

from . import tuning

logger = logging.getLogger("accel")


def _i(v, d=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return d


class BandwidthScheduler(threading.Thread):
    """后台控制环。所有对外可见状态都在 self.stats / self.view()。"""

    def __init__(self, store, manager, cfg_provider, hooks=None):
        super().__init__(name="accel-scheduler", daemon=True)
        self.store = store
        self.manager = manager
        self.cfg_provider = cfg_provider
        self.hooks = list(hooks or [])     # 每 tick 回调（分段合并器等）
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._gid_state = {}               # gid → 采样状态
        self._deferred = {}                # task_id → {"since", "until", "count"}
        self._user_paused = set()          # task_id：用户手动暂停的，调度器不碰
        self._ramp = collections.defaultdict(
            lambda: {"split": 0, "speed": 0.0, "ts": 0.0, "samples": []})
        self.log = collections.deque(maxlen=60)
        self.stats = {
            "ticks": 0, "total_speed": 0, "active": 0, "waiting": 0,
            "deferred": 0, "conn_total": 0, "last_tick": 0.0,
            "verdict": "idle", "verdict_text": "空闲", "advice": "",
            "conn_saturated": False, "peak_speed": 0,
            "session_bytes": 0, "started_at": time.time(), "error": "",
        }

    # ---------- 对外：用户操作通知 ----------
    def mark_user_paused(self, task_id: int):
        with self._lock:
            self._user_paused.add(int(task_id))
            self._deferred.pop(int(task_id), None)

    def mark_user_resumed(self, task_id: int):
        with self._lock:
            self._user_paused.discard(int(task_id))
            self._deferred.pop(int(task_id), None)

    def forget(self, task_id: int):
        with self._lock:
            self._user_paused.discard(int(task_id))
            self._deferred.pop(int(task_id), None)

    def note(self, msg: str):
        with self._lock:
            self.log.appendleft({"ts": time.time(), "msg": msg})

    # ---------- 线程体 ----------
    def stop(self):
        self._stop.set()

    def run(self):
        logger.info("[加速] 带宽调度器已启动（tick=%ss）", tuning.SCHED_TICK)
        while not self._stop.wait(tuning.SCHED_TICK):
            try:
                self.tick()
            except Exception as e:            # 任何异常都不能让控制环死掉
                self.stats["error"] = e.__class__.__name__
                logger.warning("[加速] 调度器 tick 异常: %s", e.__class__.__name__)
                time.sleep(3)

    # ---------- 单次控制周期 ----------
    def tick(self):
        cfg = dict(self.cfg_provider() or {})
        p = tuning.profile(cfg["profile"])
        max_conc = _i(cfg.get("max_concurrent")) or p["concurrent"]

        active = self.manager.tell_active()
        waiting = self.manager.tell_waiting()
        gstat = self.manager.global_stat()

        by_gid = {a.get("gid"): a for a in active}
        task_by_gid = {}
        for t in self.store.list_tasks():
            gid = t["aria2_gid"]
            if gid:
                task_by_gid[gid] = {"id": t["id"], "name": t["name"],
                                    "priority": _i(t["priority"]),
                                    "status": t["status"],
                                    "group_id": t["group_id"] or ""}
        now = time.time()

        # --- 1. 采样 + EWMA ---
        total_conn = 0
        for gid, a in by_gid.items():
            speed = _i(a.get("downloadSpeed"))
            conns = _i(a.get("connections"))
            total_conn += conns
            st = self._gid_state.get(gid)
            if st is None:
                st = {"ewma": float(speed), "slow": 0, "fast": 0, "peak": 0,
                      "born": now}
                self._gid_state[gid] = st
            st["ewma"] = (tuning.EWMA_ALPHA * speed
                          + (1 - tuning.EWMA_ALPHA) * st["ewma"])
            st["peak"] = max(st["peak"], speed)
            st["speed"] = speed
            st["conns"] = conns
        # 清掉已不在活跃列表里的 gid 采样
        for gid in list(self._gid_state):
            if gid not in by_gid:
                self._gid_state.pop(gid, None)

        # --- 2. 标记慢/快 ---
        threshold = _i(cfg.get("slow_threshold"))
        need = max(1, _i(cfg.get("slow_ticks"), tuning.DEFAULTS["slow_ticks"]))
        slow_gids, deferred_now = [], 0
        for gid, st in self._gid_state.items():
            meta = task_by_gid.get(gid)
            if meta and meta["id"] in self._user_paused:
                st["slow"] = 0
                continue
            if threshold and st["ewma"] < threshold:
                st["slow"] += 1
            else:
                st["slow"] = 0
            if st["slow"] >= need:
                slow_gids.append(gid)

        # --- 3. 让路：把慢任务踢出活跃槽 ---
        if cfg.get("yield_enabled", True):
            # 优先踢「优先级最低 + 速度最慢」的，保留跑得快的
            slow_gids.sort(key=lambda g: (
                (task_by_gid.get(g) or {}).get("priority", 0),
                self._gid_state[g]["ewma"]))
            # 只在"确实有人被压着"时才让路：活跃数超限，或 waiting 里有任务
            room_needed = max(0, len(active) - max_conc) + (1 if waiting else 0)
            for gid in slow_gids:
                if room_needed <= 0:
                    break
                meta = task_by_gid.get(gid)
                if not meta:
                    continue
                tid = meta["id"]
                if tid in self._deferred:
                    continue
                try:
                    self.manager.pause(gid)
                except Exception:
                    continue
                self._deferred[tid] = {
                    "since": now,
                    "until": now + _i(cfg.get("yield_cooldown"),
                                      tuning.DEFAULTS["yield_cooldown"]),
                    "count": self._deferred.get(tid, {}).get("count", 0) + 1,
                }
                self.note(f"让路：{meta['name'][:28]} "
                          f"（{self._gid_state[gid]['ewma'] / 1024:.0f} KB/s 偏慢，"
                          f"已暂停让出并发槽）")
                room_needed -= 1
        deferred_now = len(self._deferred)

        # --- 4. 放回：冷却到期 + 有富余槽位时恢复被让路的任务 ---
        active_after = self.manager.tell_active()
        room = max_conc - len(active_after)
        if room > 0 and self._deferred:
            # aging：让路次数多、等得久的先回来
            candidates = sorted(self._deferred.items(),
                                key=lambda kv: kv[1]["count"] * 1000
                                + (now - kv[1]["since"]), reverse=True)
            for tid, info in candidates:
                if room <= 0:
                    break
                if now < info["until"] or tid in self._user_paused:
                    continue
                row = self.store.get_task(tid)
                if not row or row["status"] not in ("downloading", "paused"):
                    self._deferred.pop(tid, None)
                    continue
                if not row["aria2_gid"]:
                    continue
                try:
                    self.manager.resume(row["aria2_gid"])
                except Exception:
                    continue
                self._deferred.pop(tid, None)
                self.note(f"放回：{row['name'][:28]}（{info['count']} 次让路后重获并发槽）")
                room -= 1

        # --- 5. 自适应连接爬坡 ---
        if cfg.get("ramp_enabled", True):
            self._ramp_tasks(cfg, by_gid, task_by_gid, now)

        # --- 6. 会话累计 + 归因 ---
        active_now = self.manager.tell_active()
        total_speed = _i(gstat.get("downloadSpeed")) or sum(
            _i(a.get("downloadSpeed")) for a in active_now)
        conn_now = sum(_i(a.get("connections")) for a in active_now)
        self.stats.update({
            "ticks": self.stats["ticks"] + 1,
            "total_speed": total_speed,
            "active": len(active_now),
            "waiting": len(self.manager.tell_waiting()),
            "deferred": deferred_now,
            "conn_total": conn_now,
            "last_tick": now,
            "peak_speed": max(self.stats["peak_speed"], total_speed),
        })
        self._attribute(cfg, active_now, total_speed, conn_now)

        for hook in self.hooks:
            try:
                hook(active_now)
            except Exception as e:
                logger.warning("[加速] 调度 hook 异常: %s", e.__class__.__name__)

    # ---------- 自适应连接爬坡 ----------
    def _ramp_tasks(self, cfg, by_gid, task_by_gid, now):
        interval = _i(cfg.get("ramp_interval"), tuning.DEFAULTS["ramp_interval"])
        up_gain = float(cfg.get("ramp_up_gain") or tuning.DEFAULTS["ramp_up_gain"])
        down_drop = float(cfg.get("ramp_down_drop") or tuning.DEFAULTS["ramp_down_drop"])
        for gid, st in list(self._gid_state.items()):
            meta = task_by_gid.get(gid)
            if not meta:
                continue
            if meta.get("group_id"):
                # 分段任务是单连接 Range 下载，改 split 会把 Range 语义搞乱
                continue
            tid = meta["id"]
            rec = self._ramp[tid]
            if rec["ts"] == 0.0:
                rec.update({"ts": now, "split": int(tuning.profile(cfg["profile"])["split"]),
                            "speed": st["ewma"]})
                continue
            if now - rec["ts"] < interval:
                continue
            prev_speed = rec["speed"] or 1.0
            gain = (st["ewma"] - prev_speed) / max(prev_speed, 1.0)
            split = rec["split"]
            p = tuning.profile(cfg["profile"])
            new_split = split
            action = ""
            if gain > up_gain and split < p["ramp_max"]:
                new_split = min(int(split * 1.6) or split + 4, p["ramp_max"])
                action = "up"
            elif gain < -down_drop and split > 4:
                new_split = max(4, split // 2)
                action = "down"
                self.stats["conn_saturated"] = True
                self.note(f"连接封顶：{meta['name'][:24]} 加到 {split} 连接后速度反降，"
                          f"回退到 {new_split}")
            if action and new_split != split:
                if self.manager.set_task_split(gid, new_split):
                    rec["samples"].append((new_split, st["ewma"]))
                    if action == "up":
                        self.note(f"爬坡：{meta['name'][:24]} 连接 {split} → {new_split}"
                                  f"（速度 +{gain * 100:.0f}%）")
                    rec.update({"split": new_split, "ts": now, "speed": st["ewma"]})
            else:
                rec.update({"ts": now, "speed": st["ewma"]})

    # ---------- 归因诊断 ----------
    def _attribute(self, cfg, active, total_speed, conns):
        d = tuning.DIAG
        limit = _i(cfg.get("overall_limit"))
        speeds = [_i(a.get("downloadSpeed")) for a in active]
        self.stats["verdict"] = "idle"
        self.stats["verdict_text"] = "空闲"
        self.stats["advice"] = ""
        if not active:
            if self._deferred:
                self.stats["verdict_text"] = "队列中（等待放回）"
            return
        if total_speed < d["idle_speed"]:
            self.stats.update(verdict="stalled", verdict_text="几乎无速度",
                              advice="任务卡在 0 速度：多半是 dlink 过期或 CDN 拒连，"
                                     "删除任务重新解析即可；持续出现请看 aria2 日志。")
            return
        if limit and total_speed >= limit * d["local_cap_ratio"]:
            self.stats.update(verdict="local_limit", verdict_text="已触顶本机带宽闸门",
                              advice=f"总速度已到设定的 {limit / 1e6:.1f} MB/s 上限，"
                                     f"调大「总带宽上限」或设为 0（不限速）可继续提升。")
            return
        if len(speeds) >= 2 and total_speed > 0:
            mean = sum(speeds) / len(speeds)
            spread = (max(speeds) - min(speeds)) / mean if mean else 1
            if spread < d["cap_spread"] and conns >= 8:
                self.stats.update(
                    verdict="account_cap", verdict_text="疑似账号级限速",
                    advice="多个任务速度几乎一样且都上不去 = 同一账号被整体限速。"
                           "解法：① 账号池加第 2 个账号（把不同文件分给不同账号）；"
                           "② 打开「分段下载」让同一个文件也用多个账号并行；"
                           "③ 换档位到 extreme 再测一次。")
                return
        if self.stats.get("conn_saturated"):
            self.stats.update(
                verdict="account_cap", verdict_text="连接已封顶（疑似账号级限速）",
                advice="连接数翻倍后速度不再增长，说明单账号带宽已到顶。"
                       "继续加连接没用，请用多账号池 + 分段下载叠加。")
            return
        if self._deferred and not self.manager.tell_waiting():
            self.stats.update(
                verdict="slot_starvation", verdict_text="并发槽被慢任务占用（已自动让路）",
                advice="调度器正在把慢任务踢出并发槽；若你希望它别被暂停，"
                       "把「慢任务让路」关掉，或把档位调成 balanced。")
            return
        per_conn = total_speed / max(conns, 1)
        if conns >= 16 and per_conn < 128 * 1024:
            self.stats.update(
                verdict="per_conn_throttle", verdict_text="单连接被限速",
                advice=f"当前 {conns} 条连接平均每条只有 {per_conn / 1024:.0f} KB/s，"
                       f"典型的分连接限速。把档位升到 turbo/extreme 加连接数即可线性叠加。")
            return
        self.stats.update(verdict="ok", verdict_text="状态正常",
                          advice="速度看起来没有被限制；若要冲更高请升档位或加账号。")

    # ---------- 对外视图 ----------
    def view(self) -> dict:
        with self._lock:
            conf_live = dict(self.stats)
            conf_live["uptime"] = int(time.time() - self.stats["started_at"])
            conf_live["ramp"] = [
                {"task_id": tid, "split": r["split"],
                 "speed": int(r["speed"])}
                for tid, r in list(self._ramp.items())[:20]]
            conf_live["log"] = list(self.log)[:15]
            return conf_live

    def per_task(self) -> dict:
        """task_id → 调度器视角（让路中 / 爬坡档位）。"""
        with self._lock:
            out = {}
            for tid, info in self._deferred.items():
                out[tid] = {"deferred": True,
                            "deferred_until": info["until"],
                            "deferred_count": info["count"]}
            for tid, r in self._ramp.items():
                out.setdefault(tid, {})["split"] = r["split"]
            return out
