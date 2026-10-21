#!/usr/bin/env python3
"""
source_audit —— 按来源随机抽样体检

用途：
  巡检器优先查“新入库/疑似/过期”，所以各来源的已检测比例并不相同；
  直接比死链率会有选择偏差。本脚本对每个来源做**随机抽样**（ORDER BY RANDOM()），
  用同一批 PanSou 检测器，得到可横向比较的死链率。

用法：
  # 预览（不写库），每来源抽 50 条
  python backend/source_audit.py --sample 50

  # 写回 validity（推荐每周跑一次）
  python backend/source_audit.py --sample 50 --apply

  # 只查两个来源
  python backend/source_audit.py --source tg:yoyokuakeduanju --source plugin:quark4k --apply

Docker 部署时（脚本已打进后端镜像）：
  docker compose exec backend python source_audit.py --sample 50 --apply

注意：
  * 只抽样 disk_type 不是 magnet/ed2k 的链接（检测源不支持）；
  * --apply 会把结果写进 link_index（与巡检器共用同一状态机）；
  * 默认每批 20 条、批间 sleep 0.2s，避免给检测源造成压力。
"""
import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime

# 允许从仓库根目录直接运行：python scripts/source_audit.py
_BACKEND = os.path.dirname(os.path.abspath(__file__))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from config import Config          # noqa: E402
from indexer import IndexDatabase  # noqa: E402
from search_engine import PansouClient  # noqa: E402


def _chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _load_sources(conn, min_total):
    rows = conn.execute("""
        SELECT source, COUNT(*) AS total
        FROM link_index
        WHERE source != '' AND disk_type NOT IN ('magnet', 'ed2k')
        GROUP BY source
        HAVING total >= ?
        ORDER BY total DESC
    """, (min_total,)).fetchall()
    return [(r["source"], int(r["total"])) for r in rows]


def _sample_links(conn, source, sample):
    rows = conn.execute("""
        SELECT url, password, disk_type, title
        FROM link_index
        WHERE source = ? AND disk_type NOT IN ('magnet', 'ed2k')
        ORDER BY RANDOM()
        LIMIT ?
    """, (source, sample)).fetchall()
    return [dict(r) for r in rows]


def main():
    parser = argparse.ArgumentParser(
        description="按来源随机抽样体检（默认 dry-run，不写库）")
    parser.add_argument("--db", default=Config.INDEX_DB_PATH,
                        help="索引库路径（默认 Config.INDEX_DB_PATH）")
    parser.add_argument("--source", action="append", default=None,
                        help="只查指定来源，可重复或逗号分隔；默认所有来源")
    parser.add_argument("--sample", type=int, default=50,
                        help="每个来源抽样条数（默认 50，范围 1-200）")
    parser.add_argument("--min-total", type=int, default=100,
                        help="只审计收录数 >= 该值的来源（默认 100）")
    parser.add_argument("--batch", type=int, default=20,
                        help="每批检测条数（默认 20，范围 1-50）")
    parser.add_argument("--sleep", type=float, default=0.2,
                        help="批间等待秒数（默认 0.2）")
    parser.add_argument("--apply", action="store_true",
                        help="把检测结果写回 link_index（默认只预览）")
    parser.add_argument("--pansou", default=None,
                        help="PanSou 地址（默认 Config.PANSOU_BASE_URL）")
    parser.add_argument("--json", default="",
                        help="可选：把汇总结果写入 JSON 文件")
    args = parser.parse_args()

    sample = max(1, min(200, args.sample))
    batch_size = max(1, min(50, args.batch))

    db = IndexDatabase(args.db)
    conn = db._conn
    client = PansouClient(base_url=args.pansou or None)

    sources = []
    if args.source:
        for item in args.source:
            sources.extend([s.strip() for s in item.split(",") if s.strip()])
    else:
        sources = [s for s, _ in _load_sources(conn, args.min_total)]

    if not sources:
        print("没有符合条件的来源（检查 --source / --min-total）。")
        return 1

    print(f"索引库: {args.db}")
    print(f"模式: {'写回 validity' if args.apply else 'dry-run（不写库）'}")
    print(f"每来源抽样: {sample} 条 · 批大小: {batch_size}\n")
    print(f"{'来源':34} {'抽样':>4} {'有效':>4} {'疑似':>4} {'失效':>4} {'不支持':>5} {'死链率':>7}")
    print("-" * 78)

    summary = []
    panou_error = None
    for source in sources:
        rows = _sample_links(conn, source, sample)
        if not rows:
            continue
        counts = Counter()
        for batch in _chunks(rows, batch_size):
            items = [{
                "disk_type": r["disk_type"],
                "url": r["url"],
                "password": r["password"] or "",
            } for r in batch]
            resp = client.check_links(items)
            if resp.get("error"):
                panou_error = resp.get("error")
                print(f"\nPanSou 检测失败: {panou_error}")
                break
            results = resp.get("results") or []
            by_url = {r.get("url"): r for r in results if r.get("url")}
            for item in items:
                r = by_url.get(item["url"]) or {}
                state = r.get("state") or "uncertain"
                summary_text = r.get("summary") or ""
                key = {
                    "ok": "ok",
                    "bad": "dead",
                    "uncertain": "suspect",
                    "unsupported": "unsupported",
                }.get(state, "other")
                counts[key] += 1
                if args.apply:
                    db.apply_check_result(item["url"], state, summary_text)
            time.sleep(max(0.0, args.sleep))

        checked = counts["ok"] + counts["suspect"] + counts["dead"]
        dead_rate = (counts["dead"] / checked * 100) if checked else None
        row = {
            "source": source,
            "sampled": len(rows),
            "ok": counts["ok"],
            "suspect": counts["suspect"],
            "dead": counts["dead"],
            "unsupported": counts["unsupported"],
            "other": counts["other"],
            "checked": checked,
            "dead_rate": round(dead_rate, 1) if dead_rate is not None else None,
        }
        summary.append(row)
        rate_text = "—" if dead_rate is None else f"{dead_rate:.1f}%"
        print(f"{source[:34]:34} {len(rows):>4} {counts['ok']:>4} "
              f"{counts['suspect']:>4} {counts['dead']:>4} "
              f"{counts['unsupported']:>5} {rate_text:>7}")

        if panou_error:
            break

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "db": args.db,
                "mode": "apply" if args.apply else "dry-run",
                "sample_per_source": sample,
                "sources": summary,
            }, f, ensure_ascii=False, indent=2)
        print(f"\nJSON 已写入: {args.json}")

    if panou_error:
        print("\n提示：确认 PanSou 在线（默认 http://127.0.0.1:8888）。")
        return 2

    print("\n完成。加 --apply 可把结果写回 link_index；建议每周跑一次。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
