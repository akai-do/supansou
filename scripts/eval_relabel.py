#!/usr/bin/env python3
"""
eval_relabel —— 用人工标签离线复算已有评测结果

用途：人工标签改完后，不必重新请求 27 次 API，直接对已保存的
docs/eval/*.json 重新计算相关率/人工重复卡数等指标。

用法：
  python scripts/eval_relabel.py \
      --input docs/eval/baseline_manual.json \
      --labels tools/eval_labels.json \
      --out docs/eval/baseline_labeled.json
"""
import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eval_search as es  # noqa: E402


def raw_card(detail):
    """把报告里的精简卡片还原成 evaluate_cards 需要的结构。"""
    validity = str(detail.get("validity") or "").strip()
    if validity == "-":
        validity = ""
    return {
        "title": detail.get("title") or "",
        "source": detail.get("source") or "",
        "disk_type": detail.get("disk_type") or "",
        "validity": validity,
        "variant_count": detail.get("variant_count") or 0,
    }


def main():
    parser = argparse.ArgumentParser(description="人工标签离线复算")
    parser.add_argument("--input", required=True)
    parser.add_argument("--labels", default=os.path.join(
        es.ROOT, "tools", "eval_labels.json"))
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        payload = json.load(f)
    with open(args.labels, "r", encoding="utf-8") as f:
        labels = json.load(f)

    results = []
    for r in payload.get("results") or []:
        cards = [raw_card(c) for c in (r.get("cards") or [])]
        data = {"total": r.get("total"), "raw_count": r.get("raw_count")}
        results.append(es.evaluate_cards(
            r.get("query") or "", r.get("category") or "other",
            data, cards, labels))

    summary, by_cat = es.aggregate(results)
    generated_at = datetime.now().isoformat(timespec="seconds")
    out_payload = {
        "generated_at": generated_at,
        "source_run": payload.get("generated_at"),
        "mode": payload.get("mode") or "full",
        "base_url": payload.get("base_url") or "",
        "force": payload.get("force"),
        "skip_stats": payload.get("skip_stats"),
        "summary": summary,
        "by_category": by_cat,
        "results": results,
    }

    out = args.out or (os.path.splitext(args.input)[0] + "_labeled.json")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(out_payload, f, ensure_ascii=False, indent=2)

    md = os.path.splitext(out)[0] + ".md"
    with open(md, "w", encoding="utf-8") as f:
        f.write(es.render_markdown(
            results, summary, by_cat,
            out_payload["mode"], generated_at))
    print(f"JSON: {out}\nMarkdown: {md}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
