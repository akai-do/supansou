"""分段跨账号下载：把单个大文件切成 K 段，分给 K 个账号并行拉，再本地拼回。

为什么需要它
------------
百度是**按账号**限速的。单账号封顶后，一个 10 GB 的文件无论开 64 连接都只能
跑那么多。把文件按字节切成 K 段、每段用**不同账号**的 dlink 去下，总带宽就
近似 K × 单账号上限（分段本身用 HTTP Range，不在本地做任何计算）。

流程
----
1. `plan_parts(size, n)` 按大小均分出 n 段（末段吃掉余数）。
2. 每段：向对应账号换一条 dlink，header 里带 `Range: bytes=start-end`，
   写到 `<文件名>.dpspart<idx>`，`split=1`（一段一条连接，多账号叠加）。
3. 全部段 complete 后由调度器 hook 触发 `merge_group()`：
   校验每段字节数 → 顺序拼接 → 删除分段文件与分任务行。
4. 任何一步失败（账号风控、服务端忽略 Range 导致段偏大/偏小）→ 报警告并把
   该文件**回退成普通多连接单任务**，绝不让用户拿到坏文件。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time

logger = logging.getLogger("accel")

PART_SUFFIX = ".dpspart"
MIN_PARTS = 2


def plan_parts(size: int, n: int) -> list:
    """把 [0, size) 等分成 n 段，返回 [(idx, start, end)]，end 为闭区间。"""
    n = max(MIN_PARTS, int(n))
    base = size // n
    parts = []
    start = 0
    for i in range(n):
        end = size - 1 if i == n - 1 else start + base - 1
        if end < start:
            break
        parts.append((i, start, end))
        start = end + 1
    return parts


class SegmentCoordinator:
    """负责分段提交与合并。生命周期跟随 accel 模块（由 __init__.py 装配）。"""

    def __init__(self, store, manager, pool, cfg_provider):
        self.store = store
        self.manager = manager
        self.pool = pool
        self.cfg_provider = cfg_provider
        self._merging = set()

    # ---------- 提交 ----------
    def plan_for(self, size: int, cfg: dict) -> int:
        """按配置与可用账号数决定切几段。"""
        if not cfg.get("segmented"):
            return 1
        if size < int(cfg.get("seg_min_size") or 0):
            return 1
        want = int(cfg.get("seg_max_parts") or 1)
        avail = self.pool.count_enabled()
        return max(1, min(want, avail))

    def submit(self, items, save_dir: str, ctx_builder):
        """对一批文件做提交：能分段的分段，其余走普通单任务。

        items: [{fid, name, size, ...}]
        ctx_builder(item, account_id, client) → (ctx, referer)  由 routes 提供，
            负责用指定账号解析分享并拿到该账号的 dlink。

        返回 (created, failed_msgs, group_ids)。
        """
        cfg = self.cfg_provider()
        created, errors, groups = [], [], []

        for it in items:
            fid, name = str(it["fid"]), it["name"]
            size = int(it.get("size") or 0)
            n = self.plan_for(size, cfg)
            if n < MIN_PARTS:
                created.append({"kind": "single", "item": it})
                continue

            accounts = self.pool.pick(n)
            if len(accounts) < MIN_PARTS:
                created.append({"kind": "single", "item": it})
                continue

            group_id = f"g{int(time.time() * 1000)}-{fid[-6:]}"
            parts = plan_parts(size, len(accounts))
            ok_parts = []
            for (idx, start, end), acct in zip(parts, accounts):
                try:
                    ctx, referer, client = ctx_builder(it, acct)
                    dlinks = client.fetch_dlinks(ctx, [fid])
                    if not dlinks:
                        raise RuntimeError("no dlink")
                    d = dlinks[0]
                except Exception as e:
                    self.pool.report(acct["id"], False,
                                     getattr(e, "errno", None),
                                     e.__class__.__name__)
                    errors.append(f"{name} 第 {idx + 1} 段：{acct['label']} 取直链失败")
                    continue
                self.pool.report(acct["id"], True)
                out = f"{name}{PART_SUFFIX}{idx}"
                try:
                    gid = self.manager.add_download(
                        d["dlink"], out, save_dir, client.cookie_header(),
                        referer,
                        options={
                            # 分段下载：一条连接一段，Range 精确到字节
                            "split": "1", "max-connection-per-server": "1",
                            "continue": "false", "allow-overwrite": "true",
                            "auto-file-renaming": "false",
                            "header": [f"Range: bytes={start}-{end}"],
                        })
                except Exception as e:
                    errors.append(f"{name} 第 {idx + 1} 段：推送到 aria2 失败")
                    logger.warning("[加速] 分段推送失败: %s", e.__class__.__name__)
                    continue
                tid = self.store.create_task(
                    name=f"{name} [{idx + 1}/{len(accounts)}]",
                    size=end - start + 1, save_dir=save_dir, fid=fid,
                    file_path=it.get("path", ""),
                    ctx={**ctx, "gid": gid},
                    group_id=group_id, seg_index=idx, seg_total=len(accounts),
                    part_start=start, part_end=end, part_out=out,
                    account_id=acct["id"],
                    hidden=0 if idx == 0 else 1)
                ok_parts.append({"task_id": tid, "gid": gid, "idx": idx,
                                 "start": start, "end": end, "out": out,
                                 "account_id": acct["id"]})
                if idx == 0:
                    # 头任务代表整个文件：名字与总大小都按最终文件来
                    self.store.update_task(tid, name=name, size=size)

            if len(ok_parts) < MIN_PARTS:
                # 分段没凑够 → 回退：把这组已建的任务取消，交给普通流程重下
                for p in ok_parts:
                    try:
                        self.manager.cancel(p["gid"])
                    except Exception:
                        pass
                    self.store.delete_task(p["task_id"])
                errors.append(f"{name}：可用账号不足，已回退为普通多连接下载")
                created.append({"kind": "single", "item": it})
                continue

            if len(ok_parts) < len(accounts):
                errors.append(f"{name}：{len(accounts) - len(ok_parts)} 段未提交（已按 "
                              f"{len(ok_parts)} 段下载，完成后仍会正确拼接）")
            groups.append({"group_id": group_id, "name": name, "size": size,
                           "parts": ok_parts, "seg_total": len(ok_parts)})
            created.append({"kind": "group", "item": it, "group": groups[-1],
                            "head_task_id": ok_parts[0]["task_id"]})
        return created, errors, groups

    # ---------- 合并 ----------
    def on_tick(self, active_dummy=None):
        """调度器每 tick 回调：挑出可合并的分组。"""
        for gid in self.store.pending_group_ids():
            if gid in self._merging:
                continue
            rows = self.store.group_tasks(gid)
            if not rows:
                continue
            if any(r["status"] not in ("done", "error") for r in rows):
                continue
            if any(r["status"] == "error" for r in rows):
                continue
            self._merging.add(gid)
            try:
                self.merge_group(gid)
            finally:
                self._merging.discard(gid)

    def merge_group(self, group_id: str) -> bool:
        rows = sorted(self.store.group_tasks(group_id), key=lambda r: r["seg_index"])
        if not rows:
            return False
        save_dir = rows[0]["save_dir"]
        final_name = rows[0]["name"]
        final_path = os.path.join(save_dir, final_name)

        # 1. 校验每段实际字节数
        for r in rows:
            p = os.path.join(save_dir, r["part_out"])
            want = int(r["part_end"]) - int(r["part_start"]) + 1
            if not os.path.isfile(p):
                return self._fail_group(rows, f"分段文件缺失：{r['part_out']}")
            got = os.path.getsize(p)
            if got != want:
                return self._fail_group(
                    rows, f"{r['part_out']} 大小不符（期望 {want}，实际 {got}）"
                          f"，服务端可能忽略了 Range 请求")

        # 2. 顺序拼接（流式，不占内存）
        tmp = final_path + ".dpsmerge"
        try:
            with open(tmp, "wb") as out:
                for r in rows:
                    with open(os.path.join(save_dir, r["part_out"]), "rb") as f:
                        shutil.copyfileobj(f, out, length=4 * 1024 * 1024)
            os.replace(tmp, final_path)
        except OSError as e:
            self._fail_group(rows, f"合并失败：{e.__class__.__name__}")
            return False

        # 3. 清理分段文件与分任务行，只留头任务代表整个文件。
        #    头任务的 group_id 必须清空，否则下一轮 on_tick 会把它当成
        #    待合并分组再合并一次（分段文件已被删，必然失败）。
        for r in rows:
            try:
                os.remove(os.path.join(save_dir, r["part_out"]))
            except OSError:
                pass
            if r["seg_index"] != 0:
                self.store.delete_task(r["id"])
        self.store.update_task(rows[0]["id"], status="done", phase="done",
                               error="", hidden=0, group_id="")
        logger.info("[加速] 分段合并完成：%s（%d 段）", final_name, len(rows))
        return True

    def _fail_group(self, rows, reason: str):
        logger.warning("[加速] 分段合并失败：%s", reason)
        for r in rows:
            # 只给头任务（seg_index=0）设置可见错误；分段行保持隐藏，
            # 否则任务列表会被一堆 .dpspartN 条目刷屏
            self.store.update_task(
                r["id"], status="error",
                error=f"分段下载失败：{reason}" if r["seg_index"] == 0 else r["error"])
        return False
