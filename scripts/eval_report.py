#!/usr/bin/env python3
"""
eval_report —— 生成搜索质量优化报告（基线 vs 最终，含 archive 通道）

用法：
  python scripts/eval_report.py \
      --baseline docs/eval/baseline_labeled.json \
      --final docs/eval/final.json \
      --archive docs/eval/archive_final3_labeled.json \
      --out docs/eval/optimization-report.md
"""
import argparse
import json
import os
from datetime import datetime


def load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def fmt(v, digits=3):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.{digits}f}"
    return str(v)


def delta(a, b, digits=3):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return f"{b - a:+.{digits}f}"
    return "—"


def metric_table(base, final):
    keys = [
        ("related_rate", "top10 相关率"),
        ("manual_dup", "人工同资源重复卡数/查询"),
        ("exact_dup", "完全重复卡数/查询"),
        ("loose_dup", "宽松重复卡数/查询"),
        ("dead_rate", "死链卡比例"),
        ("source_diversity", "来源多样性"),
        ("top_source_share", "单来源最大占比"),
        ("exact_title_rate", "完整标题命中率"),
    ]
    lines = [
        "| 指标 | 基线 | 最终 | 变化 |",
        "|---|---:|---:|---:|",
    ]
    for key, label in keys:
        a = base.get(key)
        b = final.get(key)
        lines.append(f"| {label} | {fmt(a)} | {fmt(b)} | {delta(a, b)} |")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="生成搜索质量优化报告")
    parser.add_argument("--baseline", default="docs/eval/baseline_labeled.json")
    parser.add_argument("--final", default="docs/eval/final.json")
    parser.add_argument("--archive", default="docs/eval/archive_final3_labeled.json")
    parser.add_argument("--out", default="docs/eval/optimization-report.md")
    args = parser.parse_args()

    base = load(args.baseline)
    final = load(args.final)
    archive = load(args.archive) if os.path.exists(args.archive) else {}

    bs = base.get("summary") or {}
    fs = final.get("summary") or {}
    ars = archive.get("summary") or {}

    report = f"""# 搜索质量优化报告

- 生成时间：{datetime.now().isoformat(timespec="seconds")}
- 评估集：27 个查询，覆盖影视 / 学习 / 软件 / 短词 / 长尾
- 标签：按归一化标题绑定的人工标签（`tools/eval_labels.json`），
  同资源簇按“同年份/无年份可合并、不同年份不合并”标注
- 基线：{base.get("generated_at")}（{base.get("mode")}）
- 最终：{final.get("generated_at")}（{final.get("mode")}）

## 总体结果

{metric_table(bs, fs)}

## Archive 通道（7 个问题查询）

- 查询：高等数学教材、活着、狂飙、老友记、2026考研英语一真题解析、
  肖申克的救赎4K REMUX国语、Office 安装包
- 结果：7/7 都有结果（优化前两个长查询为 0），相关率
  {fmt(ars.get("related_rate"))}，人工重复
  {fmt(ars.get("manual_dup"))} 张/查询，来源多样性
  {fmt(ars.get("source_diversity"))}。

## 按优先级做的改动

### P0：相关度过滤（改动最小、收益最大）

- `_matches_query()` 对本地索引和实时结果使用同一套口径，并修掉了
  “命中任一 ASCII 子词就放行”的兜底：
  `Office 安装包` 不再被 `Office Romance` / `The Office` / `WPS Office`
  仅凭 `office` 一个词带进结果。
- 中英混合查询要求 ASCII 词 + CJK 意图词都命中（`Office` + `安装包`）；
  “免安装中文版”这类游戏 repack 不会被当成安装包。
- 长 CJK 查询要求“前 4 字 + 强意图后缀（真题/试卷/词汇…）”或 80%
  2-gram 覆盖：`高等数学教材` 不再召回“小学语文数学教材全解”，
  `2026考研英语一真题解析` 不再召回“考研英语词汇”。
- 纯 ASCII 查询要求所有信息词命中，并按词边界匹配：
  `Windows 11` 不再接受只有 Windows 没有 11 的标题，
  `PS` 不再命中 `clips`。
- `/api/archive` 也接入同一过滤；archive SQL 召回增加 CJK 核心词/前 4 字，
  并按核心词优先排序，修复长查询在本地索引 0 结果的问题。

### P1：标题归一化与排序

- 去掉末尾随机分享码；新增发布状态词（未删减/已刮销/修复版…）、
  字幕/语轨/画质词的中文粘连清理（UHD原盘、国语配音、蓝光4K）。
- 中文标题后的英文原名（`肖申克的救赎 The Shawshank Redemption`）
  在首段 CJK >=4 字时从分组键中剔除；短名（活着/三体）不做此处理，
  避免把 `活着 VIVANT` 并进原著。
- 年份兼容：无年份 + 单一年份合并；两个以上年份按年份拆开；
  季集（第X季/Sxx）不合并。
- 排序新增/校准：精确标题 +3.5、前缀 +1.5、长尾核心词前缀；
  同前缀衍生/外传/日剧/“活着就是…”降权；营销词、游戏 repack、
  查询类别冲突降权；软件查询优先 ISO/安装包而不是教程书/管理器。
- 截断标题时补回年份，避免 `…199` 与 `…1994` 分组不一致。

### P2：主卡、来源质量与多样性

- 主卡顺序改为硬优先级：有效 > 直链 > 有密码 > 来源质量 > 网盘偏好，
  已验证的学霸盘详情页不会再被“未检测但带密码的直链”挤掉。
- 来源质量降权仍只在“已检测 >= 20、死链率 >= 50%、独家率 < 20%”
  时生效（×0.85），不做一刀切。
- 首页单来源上限 8 → 4；新增同标题簇上限 3（保留年份/季集，
  不强行拆散同年份不同版本）。

## 验证

- 21 个单元测试：`PYTHONPATH=backend python -m unittest discover -s backend/tests`
- 全量评估：`python scripts/eval_search.py --no-force --out docs/eval/final.json --compare docs/eval/baseline_labeled.json`
- 离线复算标签：`python scripts/eval_relabel.py --input docs/eval/final.json --labels tools/eval_labels.json --out docs/eval/final_labeled.json`

## 仍然存在的问题

- 2 字短查询的同前缀不同作品（`活着之上`、`活着VIVANT`）仍会通过
  “前缀”过滤；排序会降权，但 archive 结果很少时仍可能出现在 top10。
- 不同年份的版本按规则不合并；`让子弹飞` 的 2010/2011/2021 版本
  仍会占多张卡。
- 长尾查询的广告式长标题（同片不同文案）仍可能各成一张卡；
  下一步可用文件大小/文件清单做二次确认合并。
- PanSou 是异步爬取，结果随时间变化；对比时应在同一时间段跑基线/当前。
"""
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"Report: {args.out}")


if __name__ == "__main__":
    raise SystemExit(main())
