#!/usr/bin/env python3
"""
eval_search —— 搜索质量评估跑分

评估集：tools/eval_queries.json（影视/学习/软件/短词/长尾）
标签：tools/eval_labels.json（每个 query 对应 top10 的 0/1；空数组 = 用自动标签）

指标：
  * related_rate      top10 相关率（人工标签优先，否则自动标签）
  * exact_dup         top10 内归一化标题完全重复的卡数（去重后仍重复的额外卡）
  * loose_dup         去掉随机后缀/画质词后仍重复的卡数（抓“该合没合”）
  * dead_rate         top10 中整卡确认失效的比例
  * source_diversity  不同来源数 / top10 卡数
  * top_source_share  单一来源占 top10 的最大比例
  * exact_title_rate  标题包含完整查询词的比例

用法：
  # 跑基线
  python scripts/eval_search.py --no-force --out docs/eval/baseline.json

  # 改完代码后跑当前，并与基线对比
  python scripts/eval_search.py --no-force --out docs/eval/current.json \
      --compare docs/eval/baseline.json

  # 只看本地索引通道（验证 archive 相关度过滤）
  python scripts/eval_search.py --mode archive --no-force --out docs/eval/archive.json
"""
import argparse
import collections
import json
import os
import re
import sys
import time
from datetime import datetime

import requests
from requests.adapters import HTTPAdapter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 复用连接，避免连续 27 个请求把本机临时端口打满（WinError 10048）
_SESSION = requests.Session()
_SESSION.trust_env = False  # 本地服务直连，不走系统代理
_SESSION.mount("http://", HTTPAdapter(pool_connections=4, pool_maxsize=8))
_SESSION.mount("https://", HTTPAdapter(pool_connections=4, pool_maxsize=8))

# 与后端分组键大体一致的噪声词（评估脚本不依赖后端模块，保持轻量）
_QUALITY_WORDS = (
    "4k", "8k", "2k", "1080p", "720p", "2160p", "hdr10", "hdr", "uhd",
    "remux", "bluray", "webdl", "webrip", "hevc", "x265", "x264", "avc",
    "h264", "h265", "国英", "国语", "粤语", "中英", "中字", "字幕",
    "内封", "内嵌", "外挂", "双语", "简繁", "简体", "繁体", "蓝光",
    "高清", "超清", "原盘", "杜比", "视界", "全景声", "网盘", "夸克",
    "百度网盘", "阿里云盘", "迅雷网盘", "115网盘", "uc网盘",
)
_PUNCT_RE = re.compile(
    r"[\[\]【】()（）{}<>《》「」『』|·:：;；,，。.?？!！~～\"'“”‘’]+")
_RANDOM_SUFFIX_RE = re.compile(r"[_\-\s]+[A-Za-z0-9]{5,12}$")

# 长尾查询里常带版本/画质/年份修饰；自动标签先看“核心词”，避免把
# “肖申克的救赎4K REMUX国语”误判成必须标题包含全部修饰词。
_MODIFIERS = (
    "4k", "8k", "1080p", "720p", "2160p", "hdr10", "hdr", "remux",
    "bluray", "blu-ray", "webdl", "web-dl", "webrip", "hevc", "x265",
    "x264", "国英", "国语", "粤语", "中英", "中字", "字幕", "内封",
    "内嵌", "外挂", "双语", "简繁", "简体", "繁体", "蓝光", "高清",
    "超清", "原盘", "杜比", "视界", "全景声", "完整版", "高清版",
    "收藏版", "珍藏版", "解析", "第七版", "上册", "下册", "点读版",
    "1-9级", "全二季", "合集",
)


def core_query(query: str) -> str:
    """去掉版本/画质/年份修饰，保留查询意图核心词。"""
    q = (query or "").lower()
    q = re.sub(r"(?<!\d)(?:19|20)\d{2}(?!\d)", "", q)
    for w in _MODIFIERS:
        q = q.replace(w, "")
    q = _PUNCT_RE.sub("", q)
    q = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", q)
    return q or norm(query)


def norm(text: str) -> str:
    """严格归一化：小写 + 去标点/空白，用于完全重复判断。"""
    t = (text or "").lower()
    t = _PUNCT_RE.sub("", t)
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", t)


def loose_key(text: str) -> str:
    """宽松归一化：去随机分享码、画质词、标点；用于抓“该合没合”。"""
    t = (text or "").lower()
    t = _RANDOM_SUFFIX_RE.sub("", t)
    t = _PUNCT_RE.sub("", t)
    for w in _QUALITY_WORDS:
        t = t.replace(w, "")
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", t)


def title_key(title: str) -> str:
    """卡片标题指纹：归一化后取前 60 字，仅供报告展示/人工标注参考。"""
    return norm(title)[:60]


def _match_pattern(title: str, pattern: str) -> bool:
    """
    人工标签模式匹配。

    模式前缀：
      =xxx  归一化标题完全等于 xxx
      ^xxx  归一化标题以 xxx 开头（标题被截断时仍可用）
      ~xxx  归一化标题包含 xxx
      无前缀：短模式（<=4 字）按完全匹配，长模式按包含匹配
    """
    t = norm(title)
    if not t:
        return False
    raw = pattern or ""
    p = norm(raw[1:] if raw[:1] in "=^~" else raw)
    if not p:
        return False
    mode = raw[:1] if raw[:1] in "=^~" else ""
    if mode == "=":
        return t == p
    if mode == "^":
        return t.startswith(p)
    if mode == "~":
        return p in t
    if len(p) <= 4:
        return t == p
    return p in t or t.startswith(p)


def manual_labels_for(cards: list, entry) -> tuple:
    """
    按人工规则给卡片打标签，返回 (labels, sources)。

    兼容旧格式（与 top10 等长的 0/1 数组）。新格式：
      {
        "mode": "irrelevant" | "relevant",   # 未命中条目时的默认口径
        "default_label": 1 | 0,              # 可选，覆盖 mode 默认
        "irrelevant": ["~xxx", ...],         # 明确不相关
        "relevant":   ["^xxx", ...],         # 明确相关
        "clusters":   [["=xxx", "^yyy"], ...] # 人工判定的同资源簇
      }
    """
    if isinstance(entry, list):
        if len(entry) == len(cards):
            return [1 if x else 0 for x in entry], ["manual"] * len(cards)
        return None, None
    if not isinstance(entry, dict):
        return None, None

    mode = entry.get("mode") or "irrelevant"
    irrelevant = entry.get("irrelevant") or []
    relevant = entry.get("relevant") or []
    default = entry.get("default_label")
    if default is None:
        default = 0 if mode == "relevant" else 1

    labels, sources = [], []
    for card in cards:
        title = card.get("title") or ""
        label = None
        source = ""
        for pattern in relevant:
            if _match_pattern(title, pattern):
                label, source = 1, "manual-explicit"
                break
        if label is None:
            for pattern in irrelevant:
                if _match_pattern(title, pattern):
                    label, source = 0, "manual-explicit"
                    break
        if label is None:
            label, source = int(default), "manual-rule"
        labels.append(label)
        sources.append(source)
    return labels, sources


def manual_duplicate_clusters(cards: list, clusters) -> tuple:
    """
    人工同资源簇 → (每张卡的簇 id 或 None, 额外重复卡数)。

    只统计被 2 张以上卡片命中的簇；每簇第 1 张不算重复。
    """
    assigned = [None] * len(cards)
    extra = 0
    for ci, members in enumerate(clusters or []):
        hits = [
            i for i, card in enumerate(cards)
            if assigned[i] is None
            and any(_match_pattern(card.get("title") or "", p) for p in members)
        ]
        if len(hits) > 1:
            for i in hits:
                assigned[i] = ci
            extra += len(hits) - 1
    return assigned, extra


def auto_label(query: str, title: str) -> int:
    """自动相关度标签；人工标签文件优先。"""
    q = norm(query)
    t = norm(title)
    if not q or not t:
        return 0
    # 短查询：只认精确/前缀，避免“三体”被同人长标题轻易带过
    if len(q) <= 3:
        return 1 if t.startswith(q) else 0
    if q in t:
        return 1
    core = core_query(query)
    for needle in (core, q):
        if not needle:
            continue
        if len(needle) <= 3:
            if t.startswith(needle):
                return 1
            continue
        if needle in t:
            return 1
        grams = [needle[i:i + 2] for i in range(len(needle) - 1)]
        if grams and sum(1 for g in grams if g in t) / len(grams) >= 0.6:
            return 1
    return 0


def card_validity(card: dict) -> str:
    members = [card] + list(card.get("variants") or [])
    vs = [str(m.get("validity") or "") for m in members]
    if any(v == "ok" for v in vs):
        return "ok"
    if all(v == "dead" for v in vs):
        return "dead"
    if any(v == "suspect" for v in vs):
        return "suspect"
    return ""


def card_source(card: dict) -> str:
    src = str(card.get("source") or "").strip()
    if src:
        return src
    for variant in (card.get("variants") or []):
        src = str((variant or {}).get("source") or "").strip()
        if src:
            return src
    return ""


def api_search(base_url: str, query: str, mode: str, force: bool,
               skip_stats: bool, timeout: int) -> dict:
    body = {
        "kw": query,
        "page": 1,
        "page_size": 10,
        "skip_stats": skip_stats,
    }
    if mode == "archive":
        body["only_archive"] = True
    if force:
        body["force_refresh"] = True
    last_err = None
    for attempt in range(3):
        try:
            resp = _SESSION.post(
                base_url.rstrip("/") + "/api/search",
                json=body,
                timeout=timeout,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            last_err = e
            if attempt < 2:
                # Windows 连续评测可能临时端口耗尽：重建连接池 + 指数退避
                time.sleep(2.0 * (attempt + 1))
                try:
                    _SESSION.close()
                except Exception:
                    pass
                _SESSION.mount(
                    "http://", HTTPAdapter(pool_connections=4, pool_maxsize=8))
                _SESSION.mount(
                    "https://", HTTPAdapter(pool_connections=4, pool_maxsize=8))
    raise last_err


def _rate(n, d):
    return round(n / d, 4) if d else None


def evaluate_cards(query, category, data, cards, manual_labels):
    """给一批卡片算指标（API 实时结果与离线复算共用）。"""
    cards = list(cards or [])[:10]
    exact_keys = [norm(c.get("title")) for c in cards if norm(c.get("title"))]
    loose_keys = [loose_key(c.get("title")) for c in cards if loose_key(c.get("title"))]
    labels_auto = [auto_label(query, c.get("title") or "") for c in cards]
    labels = labels_auto
    label_sources = ["auto"] * len(cards)
    label_source = "auto"
    given = manual_labels.get(query) if isinstance(manual_labels, dict) else None
    manual_labels_out, manual_sources = manual_labels_for(cards, given)
    if manual_labels_out is not None:
        labels = manual_labels_out
        label_sources = manual_sources
        label_source = "manual"
    explicit = sum(1 for s in label_sources if s == "manual-explicit")
    clusters = given.get("clusters") if isinstance(given, dict) else None
    cluster_ids, manual_dup = manual_duplicate_clusters(cards, clusters)

    sources = [card_source(c) for c in cards]
    source_counter = collections.Counter(sources)
    n = len(cards)
    metrics = {
        "n": n,
        "related_rate": _rate(sum(labels), n),
        "exact_dup": len(exact_keys) - len(set(exact_keys)),
        "loose_dup": len(loose_keys) - len(set(loose_keys)),
        "manual_dup": manual_dup,
        "manual_label_rate": _rate(explicit, n),
        "dead_cards": sum(1 for c in cards if card_validity(c) == "dead"),
        "dead_rate": _rate(sum(1 for c in cards if card_validity(c) == "dead"), n),
        "source_diversity": _rate(len(source_counter), n),
        "top_source_share": _rate(max(source_counter.values()), n) if source_counter else None,
        "exact_title_rate": _rate(
            sum(1 for c in cards if norm(query) in norm(c.get("title"))), n),
    }

    detail = []
    exact_seen = set()
    loose_seen = set()
    for i, card in enumerate(cards):
        ek = norm(card.get("title"))
        lk = loose_key(card.get("title"))
        dup_exact = bool(ek and ek in exact_seen)
        dup_loose = bool(lk and lk in loose_seen)
        if ek:
            exact_seen.add(ek)
        if lk:
            loose_seen.add(lk)
        detail.append({
            "rank": i + 1,
            "title": card.get("title") or "",
            "source": card_source(card),
            "disk_type": card.get("disk_type") or "",
            "validity": card_validity(card) or "-",
            "variant_count": card.get("variant_count") or 0,
            "label": labels[i] if i < len(labels) else None,
            "label_source": label_sources[i] if i < len(label_sources) else "auto",
            "auto_label": labels_auto[i] if i < len(labels_auto) else None,
            "dup_exact": dup_exact,
            "dup_loose": dup_loose,
            "cluster": cluster_ids[i] if i < len(cluster_ids) else None,
            "title_key": title_key(card.get("title") or ""),
        })

    return {
        "query": query,
        "category": category,
        "total": data.get("total"),
        "raw_count": data.get("raw_count"),
        "label_source": label_source,
        "metrics": metrics,
        "cards": detail,
    }


def eval_one(base_url, item, mode, force, skip_stats, timeout, manual_labels):
    query = item["query"]
    category = item.get("category") or "other"
    data = api_search(base_url, query, mode, force, skip_stats, timeout)
    cards = list(data.get("results") or [])[:10]
    return evaluate_cards(query, category, data, cards, manual_labels)


def aggregate(results):
    by_category = collections.defaultdict(list)
    for r in results:
        if r["metrics"].get("n"):
            by_category[r["category"]].append(r["metrics"])

    def avg(rows, key):
        vals = [r.get(key) for r in rows if r.get(key) is not None]
        return round(sum(vals) / len(vals), 4) if vals else None

    overall_rows = [r["metrics"] for r in results if r["metrics"].get("n")]
    summary = {
        "queries": len(results),
        "queries_with_results": len(overall_rows),
        "n_cards": sum(r.get("n") or 0 for r in overall_rows),
        "related_rate": avg(overall_rows, "related_rate"),
        "exact_dup": avg(overall_rows, "exact_dup"),
        "loose_dup": avg(overall_rows, "loose_dup"),
        "manual_dup": avg(overall_rows, "manual_dup"),
        "manual_label_rate": avg(overall_rows, "manual_label_rate"),
        "dead_rate": avg(overall_rows, "dead_rate"),
        "source_diversity": avg(overall_rows, "source_diversity"),
        "top_source_share": avg(overall_rows, "top_source_share"),
        "exact_title_rate": avg(overall_rows, "exact_title_rate"),
    }
    by_cat = {}
    for cat, rows in by_category.items():
        by_cat[cat] = {
            "queries": len(rows),
            "related_rate": avg(rows, "related_rate"),
            "exact_dup": avg(rows, "exact_dup"),
            "loose_dup": avg(rows, "loose_dup"),
            "manual_dup": avg(rows, "manual_dup"),
            "manual_label_rate": avg(rows, "manual_label_rate"),
            "dead_rate": avg(rows, "dead_rate"),
            "source_diversity": avg(rows, "source_diversity"),
            "top_source_share": avg(rows, "top_source_share"),
            "exact_title_rate": avg(rows, "exact_title_rate"),
        }
    return summary, by_cat


def _fmt(v, digits=3):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.{digits}f}"
    return str(v)


def render_markdown(results, summary, by_cat, mode, generated_at):
    lines = [
        "# Search Quality Eval",
        "",
        f"- 时间：{generated_at}",
        f"- 模式：{mode}",
        f"- 查询数：{summary['queries']}（有结果 {summary['queries_with_results']}）",
        f"- top10 卡片总数：{summary['n_cards']}",
        "",
        "## 总体指标",
        "",
        "| 指标 | 值 |",
        "|---|---:|",
        f"| top10 相关率 | {_fmt(summary['related_rate'])} |",
        f"| 完全重复卡数 | {_fmt(summary['exact_dup'])} |",
        f"| 宽松重复卡数 | {_fmt(summary['loose_dup'])} |",
        f"| 人工同资源重复卡数 | {_fmt(summary['manual_dup'])} |",
        f"| 人工逐条标注覆盖率 | {_fmt(summary['manual_label_rate'])} |",
        f"| 死链卡比例 | {_fmt(summary['dead_rate'])} |",
        f"| 来源多样性 | {_fmt(summary['source_diversity'])} |",
        f"| 单来源最大占比 | {_fmt(summary['top_source_share'])} |",
        f"| 完整标题命中率 | {_fmt(summary['exact_title_rate'])} |",
        "",
        "## 分类指标",
        "",
        "| 分类 | 查询 | 相关率 | 完全重复 | 宽松重复 | 人工重复 | 死链率 | 来源多样性 | 单源最大占比 | 完整标题 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for cat, m in sorted(by_cat.items()):
        lines.append(
            f"| {cat} | {m['queries']} | {_fmt(m['related_rate'])} | "
            f"{_fmt(m['exact_dup'])} | {_fmt(m['loose_dup'])} | "
            f"{_fmt(m['manual_dup'])} | "
            f"{_fmt(m['dead_rate'])} | {_fmt(m['source_diversity'])} | "
            f"{_fmt(m['top_source_share'])} | {_fmt(m['exact_title_rate'])} |"
        )
    lines += [
        "",
        "## 单查询",
        "",
        "| 查询 | 分类 | n | 相关率 | 完全重复 | 宽松重复 | 人工重复 | 死链率 | 来源多样性 | 单源最大占比 | 完整标题 | 标签来源 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in results:
        m = r["metrics"]
        lines.append(
            f"| {r['query']} | {r['category']} | {m['n']} | "
            f"{_fmt(m['related_rate'])} | {m['exact_dup']} | {m['loose_dup']} | "
            f"{m['manual_dup']} | "
            f"{_fmt(m['dead_rate'])} | {_fmt(m['source_diversity'])} | "
            f"{_fmt(m['top_source_share'])} | {_fmt(m['exact_title_rate'])} | "
            f"{r['label_source']} |"
        )
    lines += ["", "## top10 明细", ""]
    for r in results:
        m = r["metrics"]
        lines.append(
            f"### {r['query']}（{r['category']}）— 相关 {_fmt(m['related_rate'])}，"
            f"重复 完全/宽松/人工 {m['exact_dup']}/{m['loose_dup']}/{m['manual_dup']}，"
            f"死链 {_fmt(m['dead_rate'])}"
        )
        lines.append("")
        if not r["cards"]:
            lines.append("（无结果）")
            lines.append("")
            continue
        for c in r["cards"]:
            marks = []
            if c["dup_exact"]:
                marks.append("完全重复")
            elif c["dup_loose"]:
                marks.append("宽松重复")
            if c.get("cluster") is not None:
                marks.append(f"人工簇{c['cluster'] + 1}")
            if c["validity"] == "dead":
                marks.append("死链")
            mark = (" ⚠ " + "/".join(marks)) if marks else ""
            lines.append(
                f"{c['rank']}. [{c['label']}:{c.get('label_source', 'auto')}] "
                f"{c['title']} "
                f"（{c['source'] or '未知来源'} / {c['disk_type'] or '?'} / "
                f"{c['validity']} / v={c['variant_count']}）{mark}"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def render_compare(baseline, current, summary_now):
    base_summary = baseline.get("summary") or {}
    keys = [
        ("related_rate", "top10 相关率"),
        ("exact_dup", "完全重复卡数"),
        ("loose_dup", "宽松重复卡数"),
        ("manual_dup", "人工同资源重复卡数"),
        ("dead_rate", "死链卡比例"),
        ("source_diversity", "来源多样性"),
        ("top_source_share", "单来源最大占比"),
        ("exact_title_rate", "完整标题命中率"),
    ]
    lines = [
        "# Search Eval Compare",
        "",
        f"- 基线：{baseline.get('generated_at')}（{baseline.get('mode')}）",
        f"- 当前：{current.get('generated_at')}（{current.get('mode')}）",
        "",
        "| 指标 | 基线 | 当前 | 变化 |",
        "|---|---:|---:|---:|",
    ]
    for key, label in keys:
        b = base_summary.get(key)
        c = summary_now.get(key)
        if isinstance(b, (int, float)) and isinstance(c, (int, float)):
            delta = c - b
            lines.append(f"| {label} | {_fmt(b)} | {_fmt(c)} | {delta:+.3f} |")
        else:
            lines.append(f"| {label} | {_fmt(b)} | {_fmt(c)} | — |")

    base_map = {r["query"]: r for r in baseline.get("results") or []}
    changed = []
    for r in current.get("results") or []:
        b = base_map.get(r["query"])
        if not b:
            continue
        bm = b.get("metrics") or {}
        cm = r.get("metrics") or {}
        b_top = (b.get("cards") or [{}])[0].get("title", "")
        c_top = (r.get("cards") or [{}])[0].get("title", "")
        if b_top != c_top:
            changed.append((r["query"], b_top, c_top))
    lines += ["", "## Top1 变化", ""]
    if not changed:
        lines.append("（无变化）")
    else:
        for query, before, after in changed:
            lines.append(f"- **{query}**")
            lines.append(f"  - 前：{before}")
            lines.append(f"  - 后：{after}")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="搜索质量评估跑分")
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--queries", default=os.path.join(
        ROOT, "tools", "eval_queries.json"))
    parser.add_argument("--labels", default=os.path.join(
        ROOT, "tools", "eval_labels.json"))
    parser.add_argument("--mode", choices=("full", "archive"), default="full")
    parser.add_argument("--force", dest="force", action="store_true",
                        help="强制刷新（跳过搜索缓存）")
    parser.add_argument("--no-force", dest="force", action="store_false")
    parser.set_defaults(force=False)
    parser.add_argument("--count-stats", dest="skip_stats", action="store_false",
                        help="把评测请求计入来源曝光（默认不计）")
    parser.set_defaults(skip_stats=True)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--sleep", type=float, default=0.8,
                        help="查询之间的等待秒数（默认 0.8，避免本机端口耗尽）")
    parser.add_argument("--out", default=os.path.join(
        ROOT, "docs", "eval", "last_run.json"))
    parser.add_argument("--md", default="")
    parser.add_argument("--compare", default="",
                        help="与之前的 JSON 基线对比")
    parser.add_argument("--retry-errors", default="",
                        help="复用该 JSON 的成功结果，只重跑 error 的查询")
    parser.add_argument("--write-labels", default="",
                        help="输出人工标签模板（空数组）")
    args = parser.parse_args()

    with open(args.queries, "r", encoding="utf-8") as f:
        queries = [q for q in json.load(f) if not str(q.get("query", "")).startswith("_")]
    manual_labels = {}
    if args.labels and os.path.exists(args.labels):
        with open(args.labels, "r", encoding="utf-8") as f:
            manual_labels = json.load(f)

    results = []
    total = len(queries)
    previous = {}
    if args.retry_errors and os.path.exists(args.retry_errors):
        with open(args.retry_errors, "r", encoding="utf-8") as f:
            prev_payload = json.load(f)
        previous = {
            r.get("query"): r
            for r in (prev_payload.get("results") or [])
        }
    for i, item in enumerate(queries, 1):
        query = item.get("query", "")
        prev = previous.get(query)
        if prev is not None and not prev.get("error"):
            print(f"[{i}/{total}] {query} (reuse)", file=sys.stderr, flush=True)
            results.append(prev)
            continue
        print(f"[{i}/{total}] {query}", file=sys.stderr, flush=True)
        try:
            results.append(eval_one(
                args.base_url, item, args.mode, args.force,
                args.skip_stats, args.timeout, manual_labels))
        except Exception as e:
            print(f"  ERROR: {e}", file=sys.stderr, flush=True)
            results.append({
                "query": query,
                "category": item.get("category") or "other",
                "total": None,
                "raw_count": None,
                "label_source": "auto",
                "metrics": {
                    "n": 0, "related_rate": None, "exact_dup": 0,
                    "loose_dup": 0, "dead_cards": 0, "dead_rate": None,
                    "source_diversity": None, "top_source_share": None,
                    "exact_title_rate": None,
                },
                "cards": [],
                "error": str(e),
            })
        time.sleep(max(0.0, args.sleep))

    summary, by_cat = aggregate(results)
    generated_at = datetime.now().isoformat(timespec="seconds")
    payload = {
        "generated_at": generated_at,
        "mode": args.mode,
        "base_url": args.base_url,
        "force": args.force,
        "skip_stats": args.skip_stats,
        "summary": summary,
        "by_category": by_cat,
        "results": results,
    }

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    md_path = args.md or (os.path.splitext(args.out)[0] + ".md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(render_markdown(results, summary, by_cat, args.mode, generated_at))
    print(f"\nJSON: {args.out}\nMarkdown: {md_path}")

    if args.compare:
        with open(args.compare, "r", encoding="utf-8") as f:
            baseline = json.load(f)
        compare_md = render_compare(baseline, payload, summary)
        compare_path = os.path.splitext(args.out)[0] + ".compare.md"
        with open(compare_path, "w", encoding="utf-8") as f:
            f.write(compare_md)
        print(f"\n{compare_md}")
        print(f"Compare: {compare_path}")

    if args.write_labels:
        tmpl = {
            "_readme": (
                "人工标签：mode=irrelevant 表示未列出的卡片默认相关；"
                "mode=relevant 表示未列出的默认不相关。"
                "irrelevant/relevant 支持 =精确 ^前缀 ~包含；"
                "clusters 是人工判定的同资源簇（每簇第 1 张不算重复）。"
            )
        }
        for r in results:
            entry = {
                "mode": "irrelevant",
                "default_label": 1,
                "irrelevant": [],
                "relevant": [],
                "clusters": [],
                "titles": [
                    {"rank": c["rank"], "title": c["title"],
                     "key": c.get("title_key") or title_key(c["title"])}
                    for c in r["cards"]
                ],
            }
            tmpl[r["query"]] = entry
        os.makedirs(os.path.dirname(os.path.abspath(args.write_labels)),
                    exist_ok=True)
        with open(args.write_labels, "w", encoding="utf-8") as f:
            json.dump(tmpl, f, ensure_ascii=False, indent=2)
        print(f"Labels template: {args.write_labels}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
