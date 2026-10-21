"""
DuPanSou-Archive API 路由

提供以下接口：
  POST /api/search       — 融合搜索（实时+历史）
  GET  /api/archive      — 浏览历史索引
  GET  /api/stats        — 统计数据
  GET  /api/health       — 服务健康状
  POST /api/check/run    — 手动触发巡检

PanSou Search API（来自 Go 后端）是搜索引擎基础设施，本 API 是上层应用。
"""
import logging
import os
import re
import time
import queue
import json
import threading

import requests
from concurrent.futures import ThreadPoolExecutor
from flask import Blueprint, request, jsonify, redirect, Response, stream_with_context
from datetime import datetime
from config import Config
from source_stats import classify_category
from access import is_station     # 站长判定（与 accel 蓝图共用一份实现）
from imgcache import UnsafeURL   # noqa: F401  (异常类型供上层判断)
import imgcache
from search_engine.xuebapan import xuebapan_client as _xuebapan
from search_engine.spell import suggest_correction as _suggest_correction
from search_engine.title_correct import (
    suggest_title_corrections as _suggest_title_corrections,
)

logger = logging.getLogger("api")

api_bp = Blueprint("api", __name__)

# ==========================================
# 搜索结果缓存（进程内）
# key → (时间戳, payload)；TTL 内同关键词翻页直接命中缓存，
# 不再重复请求 PanSou，也避免每次翻页重复写索引。
# ==========================================
_search_cache = {}
_search_cache_lock = threading.Lock()
_SEARCH_CACHE_MAX = 200

# 同关键词并发搜索合并（in-flight 去重）：
# 首搜还没结束时，用户连续点网盘筛选、连续刷新页面、开多个标签搜同一个词，
# 都会在缓存写入前到达。若每个请求都从头跑一遍管线，一次点击就会放大成
# ~9 个并发 PanSou 查询；点几次筛选就能把上游连接池和线程打满，随后
# 浏览器的 fetch 就会表现为 "Failed to fetch" / 页面暂时无法连接。
# 这里让后来者等待同一条管线并直接复用其结果池。
_search_inflight = {}
_search_inflight_lock = threading.Lock()
_SEARCH_INFLIGHT_MAX_WAIT = 35.0   # 后来者最多等多久（秒）
_SEARCH_INFLIGHT_STALE = 40.0      # 主跑管线异常退出后的登记过期时间


def _cache_put(key: str, payload: dict):
    """写入缓存；超容量时淘汰最旧的一半"""
    with _search_cache_lock:
        if len(_search_cache) >= _SEARCH_CACHE_MAX:
            stale = sorted(_search_cache.items(), key=lambda kv: kv[1][0])
            for k, _ in stale[:_SEARCH_CACHE_MAX // 2]:
                _search_cache.pop(k, None)
        _search_cache[key] = (time.time(), payload)
    # 唤醒等待同一关键词的并发请求：完整结果池已就绪，直接复用
    with _search_inflight_lock:
        ent = _search_inflight.pop(key, None)
    if ent is not None:
        ent["result"] = (payload, False, payload.get("_correction"))
        ent["event"].set()


# 网盘类型优先级：越靠前越常用/体验越好，用于跨源排序打破平局
DISK_PRIORITY = {
    "quark": 0, "aliyun": 1, "baidu": 2, "xunlei": 3, "115": 4,
    "123": 5, "tianyi": 6, "uc": 7, "pikpak": 8, "magnet": 9,
}


def _split_terms(keyword: str) -> list:
    """
    中文轻量分词：把关键词拆成有意义的子词单元。
    策略：连续 ASCII 一个整体；中文按滑动窗口切 2-3 字词 + 整段。
    """
    terms = []
    ascii_buf = []

    def flush():
        if ascii_buf:
            w = "".join(ascii_buf).lower()
            if w not in terms:
                terms.append(w)
            ascii_buf.clear()

    chars = list(keyword)
    i = 0
    n = len(chars)
    while i < n:
        c = chars[i]
        if ord(c) < 128:
            ascii_buf.append(c)
            i += 1
            continue
        flush()
        seg_start = i
        while i < n and ord(chars[i]) >= 128:
            i += 1
        seg = "".join(chars[seg_start:i])
        L = len(seg)
        if L > 1:
            for j in range(L - 1):
                t = seg[j:j+2]
                if t not in terms:
                    terms.append(t)
            if L > 2:
                for j in range(L - 2):
                    t = seg[j:j+3]
                    if t not in terms:
                        terms.append(t)
        elif seg and seg not in terms:
            terms.append(seg)
    flush()
    return terms


def _build_search_variants(keyword: str) -> list:
    """
    为实时搜索生成多种查询变体，提升 PanSou 召回率：
      1. 原始完整关键词（最相关）
      2. 若含多词，拆分后的子词独立搜索（如"蜘蛛侠电影" → 蜘蛛侠, 电影）
      3. 子词组合的子集
    去重后返回，最多不超过 5 个变体，控制并发开销。
    """
    kw = keyword.strip()
    if not kw:
        return []
    variants = [kw]
    terms = _split_terms(kw)
    # 过滤掉过于短的噪声单字（如"片""的"）和纯数字/符号噪声
    def _noise(t):
        if len(t) < 2:
            return True
        # 纯数字或数字+空格的噪声（如 "3 "）
        if re.sub(r"\d\s*", "", t) == "":
            return True
        return False
    meaningful = [t for t in terms if not _noise(t)][:6]

    # 若原词是连接多词（含明显分隔或超长），加入子词独立查询
    if len(meaningful) >= 2:
        for t in meaningful:
            if t != kw and len(t) <= 8 and t not in variants:
                variants.append(t)

    # 若原始词超长（>6字），尝试组合前几个有意义的词
    if len(kw) > 6 and len(meaningful) >= 2:
        head = meaningful[0] + meaningful[1] if len(meaningful[0]) <= 4 else meaningful[0]
        if head not in variants and head != kw:
            variants.append(head)

    # 限制变体数量，原词永远第一位
    # ★ 该 PanSou 部署对含空格查询一律返回 0：主词带空格时补一个去空格变体
    if " " in kw:
        nospace = kw.replace(" ", "")
        if nospace and nospace not in variants:
            variants.insert(1, nospace)
    return variants[:6]


def _build_supplement_variants(keyword: str) -> list:
    """
    补充变体：给主关键词追加通用修饰词扩搜（如 "python教程"、"海贼王合集"）。
    注意用无空格拼接——该 PanSou 部署对含空格查询一律返回 0。
    扩的是召回不是意图——补充结果仍须通过原词相关度过滤（_matches_query），
    标题里不含原词的资源不会被采纳。
    """
    kw = keyword.strip()
    if not kw or len(kw) > 20:
        return []
    if all(ord(c) < 128 for c in kw):
        mods = Config.SUPPLEMENT_MODIFIERS_ASCII
    else:
        mods = Config.SUPPLEMENT_MODIFIERS_CJK
    out = []
    for m in mods:
        m = m.strip()
        if not m:
            continue
        q = f"{kw}{m}"          # 无空格拼接（源不支持空格查询）
        if q not in out:
            out.append(q)
    return out[:3]


def _link_quality(it: dict) -> float:
    """URL 撞车时选信息更全的一条：带密码 > 带消息图 > 标题完整 > 有时间"""
    q = 0.0
    if it.get("password"):
        q += 2.0
    if it.get("msg_image"):
        q += 0.4  # 有消息图的优先，避免去重时把封面挤掉
    q += min(len(str(it.get("title") or "")), 80) / 40.0
    if it.get("datetime"):
        q += 1.0
    return q



_GENERIC_SHORT_QUERIES = {
    "电影", "电视", "电视剧", "动漫", "动画", "综艺", "短剧", "音乐",
    "歌曲", "软件", "游戏", "小说", "电子书", "教程", "课程", "资源",
    "合集", "美剧", "韩剧", "日剧", "国剧",
}

_LEADING_LABELS = (
    "资源名称", "标题", "名称", "剧名", "影视", "电影", "电视剧", "短剧",
    "国剧", "美剧", "日剧", "韩剧", "动漫", "动画", "综艺", "课程",
    "教程",
)

_LABEL_TAGS = {
    "电影", "电视剧", "短剧", "国剧", "美剧", "日剧", "韩剧",
    "动漫", "动画", "综艺", "影视", "资源", "课程", "教程",
}

# 长尾查询里的版本/画质/年份修饰：匹配时先看“核心词”，
# 避免“肖申克的救赎4K REMUX国语”被 4K/REMUX 子词带回一堆无关电影。
_QUERY_MODIFIERS = (
    "4k", "8k", "1080p", "720p", "2160p", "hdr10", "hdr", "remux",
    "bluray", "blu-ray", "webdl", "web-dl", "webrip", "hevc", "x265",
    "x264", "国英", "国语", "粤语", "中英", "中字", "字幕", "内封",
    "内嵌", "外挂", "双语", "简繁", "简体", "繁体", "蓝光", "高清",
    "超清", "原盘", "杜比", "视界", "全景声", "完整版", "解析",
    "第七版", "上册", "下册", "点读版", "1-9级", "全二季",
)


def _core_query_text(keyword: str) -> str:
    """去掉版本/画质/年份修饰，保留查询意图核心词。"""
    q = (keyword or "").lower()
    q = re.sub(r"(?<!\d)(?:19|20)\d{2}(?!\d)", "", q)
    for w in _QUERY_MODIFIERS:
        q = q.replace(w, "")
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", q)


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= c <= "\u9fff" for c in (text or ""))


# 长核心词末尾的“强意图词”：查询“考研英语真题”时，
# 只含“考研英语”的词汇书/课程不应算命中。
_STRONG_INTENT_SUFFIXES = (
    "真题解析", "真题", "试卷", "单词", "词汇",
)


def _core_query_match(title: str, core_norm: str) -> bool:
    """
    标题是否命中核心词。

    - 核心词完整出现 → 命中
    - 长核心词（>=5 字）：前 4 字（如“高等数学”“考研英语”）出现，
      且末尾强意图词（真题/教材/教程…）也出现，或 2-gram 覆盖 >= 80%。
      用于挡住“小学数学教材全解”“考研英语词汇”这类只共享前半段的噪声。
    - 短核心词：2-gram 覆盖 >= 60% 即可，保留召回。
    """
    if not core_norm:
        return False
    title_norm = _normalize_title_key(_clean_title_labels(title))
    if not title_norm:
        return False
    if core_norm in title_norm:
        return True
    grams = [core_norm[i:i + 2] for i in range(len(core_norm) - 1)]
    if not grams:
        return False
    coverage = sum(1 for g in grams if g in title_norm) / len(grams)

    suffix_ok = True
    for suffix in _STRONG_INTENT_SUFFIXES:
        if core_norm.endswith(suffix):
            suffix_ok = suffix in title_norm
            break

    if len(core_norm) >= 5:
        head = core_norm[:min(4, len(core_norm) - 1)]
        head_ok = bool(head and head in title_norm)
        if head_ok and suffix_ok:
            return True
        return suffix_ok and coverage >= 0.8
    return coverage >= 0.6


def _is_short_cjk_query(keyword: str) -> bool:
    """2-3 个纯汉字的短查询（如 三体/活着/狂飙/老友记）。"""
    kw = (keyword or "").strip()
    return (0 < len(kw) <= 3
            and all("\u4e00" <= c <= "\u9fff" for c in kw))


def _clean_title_labels(title: str) -> str:
    """去掉标题开头的标签/画质角标，但保留年份（分组/核心词匹配用）。"""
    t = str(title or "").strip()
    t = re.sub(r"^[\s\W_]+", "", t, flags=re.UNICODE)
    # 去掉开头的“电影｜/电影：/电视剧名称：”等分类标签
    t = re.sub(
        r"^(?:电影|电视剧|动漫|动画|综艺|纪录片|短剧)"
        r"\s*(?:名称)?\s*[｜|:：\-]\s*", "", t)
    # 去掉开头的“资源名称：/标题：/电视剧：”等标签
    for label in _LEADING_LABELS:
        t = re.sub(rf"^{label}\s*[:：]\s*", "", t)
    # 去掉开头的【国剧】【4K】【原盘】等短角标；
    # 如果角标里本身含查询词（如 [三体 3 Body Problem]），保留不动。
    m = re.match(r"^\s*[\[【]([^\]】]{1,12})[\]】]\s*", t)
    if m:
        tag = m.group(1).strip().lower()
        if tag in _LABEL_TAGS or all(
                part in _QUALITY_TOKENS for part in tag.split()):
            t = t[m.end():]
    return t.strip()


def _clean_title_start(title: str) -> str:
    """去掉标题开头的标签/年份/画质角标，便于判断“是否以查询词开头”。"""
    t = _clean_title_labels(title)
    # 去掉开头的 4 位年份（2026剪映… → 剪映…）
    t = re.sub(r"^(?:19|20)\d{2}[\s._\-·:：]*", "", t)
    return t.strip()


def _title_starts_with(title: str, keyword: str) -> bool:
    """归一化后标题是否以查询词开头（允许年份/标签/画质角标前缀）。"""
    t = _normalize_title_key(_clean_title_start(title))
    k = _normalize_title_key(keyword)
    return bool(t and k and t.startswith(k))


_ASCII_TOKEN_RE_CACHE = {}


def _ascii_token_in(text: str, token: str) -> bool:
    """
    ASCII 词命中判定：短词按词边界，长词按子串。

    短词用子串会把 "PS" 匹配到 "clips"、把 "11" 匹配到 "2011"，
    是垃圾查询混进结果的主要通道之一。
    """
    token = (token or "").lower()
    if not token:
        return False
    if len(token) > 3:
        return token in text
    pat = _ASCII_TOKEN_RE_CACHE.get(token)
    if pat is None:
        pat = re.compile(
            r"(?<![0-9a-z])" + re.escape(token) + r"(?![0-9a-z])")
        _ASCII_TOKEN_RE_CACHE[token] = pat
    return bool(pat.search(text))


# 这些 CJK 词只是泛化后缀，不构成查询意图；中英混合查询里可以缺席。
_GENERIC_QUERY_CJK = {
    "系统", "版本", "资源", "合集", "下载", "中文", "最新", "完整", "免费",
}


def _mixed_query_match(title: str, core_norm: str, title_source: str) -> bool:
    """
    中英混合查询：ASCII 词必须命中，CJK 部分至少命中一个有意义的 2-gram。

    这样 "Office 安装包" 不会再被 "Office Romance"/"WPS Office" 仅凭
    office 一个词带走；同时 "Python入门教程" 仍能接受 "Python教程"。
    """
    ascii_runs = re.findall(r"[0-9a-z]+", core_norm)
    cjk_runs = re.findall(r"[\u4e00-\u9fff]+", core_norm)
    title_norm = _normalize_title_key(_clean_title_labels(title))

    # “安装包”查询不接受“免安装中文版”（游戏 repack/绿色版），
    # 除非标题本身也写了“安装包”。
    if ("安装包" in core_norm and "免安装" in title_norm
            and "安装包" not in title_norm):
        return False

    for run in ascii_runs:
        if len(run) < 2:
            continue
        if not _ascii_token_in(title_source, run):
            return False

    meaningful = [r for r in cjk_runs if r not in _GENERIC_QUERY_CJK]
    if not meaningful:
        return True

    for run in meaningful:
        if len(run) == 1:
            if run in title_norm:
                return True
            continue
        grams = [run[i:i + 2] for i in range(len(run) - 1)]
        hits = sum(1 for g in grams if g in title_norm)
        if len(run) <= 4:
            # 短 CJK 段（安装包/入门教程）：命中任一双字词即可
            if hits:
                return True
        elif hits / len(grams) >= 0.5:
            return True
    return False


def _matches_query(item, keyword, tokens):
    """
    判定一条结果与查询是否真正相关（而非仅被某个子词顺带命中）。

    用于过滤掉"变体搜索"带回来的偏差结果，例如搜"蜘蛛侠"时，
    PanSou 对"电影"子词返回的海量与本查询无关的影视链接。
    注意：子词只匹配标题/来源——网盘分享码是随机字符串，
    若参与匹配，垃圾查询会因分享码恰好含子串而漏进无关结果。

    短中文查询（三体/活着/狂飙）额外要求标题以查询词开头：
    “还活着”“古宅老友记”“港综之无间道”这类同人/衍生/无关内容
    不再靠“包含子串”混进结果。泛查询（电影/软件/教程）不做此限制。

    长查询/中英混合查询（Office 安装包、高等数学教材）要求命中核心词，
    不再降级为“任一 ASCII 子词”：否则 "Office Romance" 仅凭 office
    一个词就能混进 "Office 安装包" 的结果。

    长尾兜底由调用方控制：tokens 用 _tokenize_query(kw, extended=True)
    生成的扩展表（含 CJK 信息词）即宽松模式，仅在严格过滤颗粒无收时使用。
    """
    if (_is_short_cjk_query(keyword)
            and (keyword or "").strip() not in _GENERIC_SHORT_QUERIES):
        return _title_starts_with(item.get("title") or "", keyword)

    title_source = " ".join([
        str(item.get("title", "") or ""),
        str(item.get("source", "") or ""),
    ]).lower()
    full_blob = title_source + " " + str(item.get("url", "") or "").lower()
    kw_low = (keyword or "").lower().strip()
    if kw_low:
        # 短 ASCII 词（PS/AI）必须按词边界匹配，否则 "PS" 会命中 clips。
        if not _has_cjk(kw_low) and " " not in kw_low and len(kw_low) <= 3:
            if _ascii_token_in(full_blob, kw_low):
                return True
        elif kw_low in full_blob:
            return True

    # 长查询有 CJK 核心词时，忽略 4K/REMUX/国语 这类修饰子词，
    # 直接看“核心词”是否命中；否则仍用信息词过滤。
    core = _core_query_text(keyword)
    core_norm = _normalize_title_key(core)
    if len(core_norm) >= 3:
        if _has_cjk(core_norm):
            # 中英混合：ASCII 词 + CJK 意图词都要命中；
            # 纯 CJK：沿用核心词/2-gram 覆盖率。
            if re.search(r"[0-9a-z]", core_norm):
                return _mixed_query_match(
                    item.get("title") or "", core_norm, title_source)
            return _core_query_match(item.get("title") or "", core_norm)
        # 纯 ASCII 查询：所有信息词都要命中。
        # 例："Windows 11" 不再接受只有 Windows 没有 11 的标题；
        #     "Photoshop 2024" 不再接受 2023 版。
        if tokens:
            return all(_ascii_token_in(title_source, t) for t in tokens)
        title_norm = _normalize_title_key(
            _clean_title_labels(item.get("title") or ""))
        return core_norm in title_norm
    if len(core_norm) == 2:
        # 2 字 ASCII 查询（PS/AI）：只认词边界，避免命中 clips/apps。
        return _ascii_token_in(title_source, core_norm)
    if tokens:
        return any(_ascii_token_in(title_source, t) for t in tokens)
    return False


def _tokenize_query(keyword: str, extended: bool = False) -> list:
    """
    提取用于相关性过滤的信息词。

    strict（默认）：仅 ASCII 词——网盘分享码/英文词的可靠信号。
    extended（长尾兜底）：追加 CJK 连续段（>=2 字）——长书名/教材名的
    拆词命中靠它才能通过过滤；只应在严格过滤 0 结果后使用，
    否则垃圾查询里的真实中文词（如"不存在"）会漏进模糊结果。
    """
    tokens = []
    ascii_buf = []
    cjk_buf = []

    def flush_ascii():
        if ascii_buf:
            w = "".join(ascii_buf).lower()
            if len(w) >= 2 and not re.fullmatch(r"[\d\s]+", w) and w not in tokens:
                tokens.append(w)
            ascii_buf.clear()

    def flush_cjk():
        if cjk_buf:
            seg = "".join(cjk_buf)
            if len(seg) >= 2 and seg not in tokens:
                tokens.append(seg)
            cjk_buf.clear()

    for c in list(keyword):
        if ord(c) < 128:
            flush_cjk()
            ascii_buf.append(c)
        else:
            flush_ascii()
            cjk_buf.append(c)
    flush_ascii()
    if extended:
        flush_cjk()
    return tokens


def _iter_collected_links(collected):
    """展平 wave collected 里所有 ok 项的链接"""
    for item in collected:
        if item[0] == "ok":
            for ln in (item[2] or []):
                yield ln


def _token_hits(item, keyword, tokens):
    """扩展信息词在标题/来源中的命中数；完整原词命中直接视为全命中"""
    title_source = " ".join([
        str(item.get("title", "") or ""),
        str(item.get("source", "") or ""),
    ]).lower()
    if keyword and keyword.lower() in (title_source + " " +
                                       str(item.get("url", "") or "").lower()):
        return len(tokens) + 1

    def hit(t):
        return (_ascii_token_in(title_source, t)
                if all(ord(c) < 128 for c in t) else t in title_source)

    return sum(1 for t in tokens if hit(t))


def _shorten_title(title: str, maxlen: int = 46) -> str:
    """把超长广告式标题精简为搜索摘要，避免搜索结果页被广告文案刷屏"""
    if not title:
        return title
    t = title.strip().replace("\n", " ").replace("  ", " ")
    # 截断到第一个明显的分隔符/标签堆积处
    for cut in ("💾", "💬", "📁", "💬💬", "📜", "⬇", "🏷", "👉", "👇"):
        idx = t.find(cut)
        if 0 < idx < maxlen:
            t = t[:idx].rstrip(" ·：:,，. ")
            break
    if len(t) > maxlen:
        cut = t[:maxlen].rstrip(" ·：:,，. ")
        # 截断常把末尾年份切掉，导致“同片不同版本”分组键不一致；
        # 年份是资源身份的一部分，截断后补回。
        year = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)", t)
        if year and year.group(1) not in cut:
            # 截断点可能落在年份中间（…199），先去掉残余前缀再补完整年份
            cut = re.sub(r"(?:19|20)\d{0,2}$", "", cut)
            cut = cut.rstrip(" ·：:,，. ")
            cut = f"{cut} {year.group(1)}"
        t = cut + "…"
    return t


_NEGATIVE_TITLE_MARKERS = (
    "盘点", "十大", "同人", "港综", "综影视", "开局",
    "作者：", "作者:", ".txt", "解说", "混剪", "短剧", "AI短剧",
    # 短查询的衍生/外传/同人常以“XX之…/XX风云/XX外传”出现；
    # 查询本身包含这些词时不降权（见 score）。
    "风云", "外传", "前传", "后传", "衍生", "动漫版", "漫画版",
    "游戏版",
)

# 短查询命中前缀后，紧跟这些词通常意味着是另一部作品/衍生内容，
# 例如 活着 → 活着就是爱 / 活着为了讲述；狂飙 → 狂飙之浴血玫瑰。
_SPINOFF_TAIL_RE = re.compile(
    r"^(?:之|的|就是|为了|不是|便是|才是|还是|也是|都是|只是|不再|"
    r"没有|如何|怎样|怎么|为何|什么|真的|其实|可以|应该|必须|一个|"
    r"一种|那些|这个|那个|我们|你们|他们|自己|世界|人生|生活|故事|"
    r"时候|地方|东西|事情|问题|意思|意义|灵魂|再见|回来|离开|重生|"
    r"崛起|风云|外传|前传|后传|衍生|同人|"
    r"日剧|美剧|韩剧|港剧|台剧|英剧|泰剧|国剧|"
    r"动漫|动画|漫画|游戏|短剧|舞台剧|话剧|电影版|电视剧版)"
)

# 前缀后紧跟的“版本/季集/画质”尾巴是正常变体，不应按衍生内容降权。
_EDITION_TAIL_RE = re.compile(
    r"^(?:\d{4}|第[一二三四五六七八九十0-9]+[季部集]|"
    r"[sS]\d+(?:[eE]\d+)?|[sS][eE]\d+|[eE]\d+|全\d+集|\d+集全|"
    r"特别版|精校版|修订版|"
    r"美版|日版|韩版|港版|台版|国版|网飞版|腾讯版|爱奇艺版|"
    r"国语|粤语|英语|中字|字幕|无删减|未删减|删减|导演|加长|"
    r"修复|重制|完整|高清|超清|蓝光|原盘|4[kK]|8[kK]|1080[pP]|"
    r"2160[pP]|720[pP]|hdr|remux|web|bd|dvd|合集|全集|系列|"
    r"剧场版|电影版|电视剧|动漫|动画|综艺|纪录片)"
)

# 营销/导流文案：标题里出现越多，越可能是广告贴而非资源本身。
_MARKETING_MARKERS = (
    "点击保存", "速存", "限时", "失效补", "关注公众号", "微信公众号",
    "加微信", "qq群", "QQ群", "免费领取", "更多资源", "整理不易",
    "一键三连", "点赞", "收藏", "转发", "私信", "补链", "防失联",
    "备用", "长按", "扫码", "提取码", "复制这段", "打开app", "打开APP",
    "夸克app", "夸克APP",
)

# 软件查询里“可安装/可运行”的信号：同名前缀下，ISO/安装包应排在
# 教程书、管理器工具之前。
_SOFTWARE_ASSET_MARKERS = (
    "iso", "msdn", "vlsc", "x64", "x86", "64位", "32位", "安装包",
    "安装工具", "installer", "portable", "绿色版", "精简版",
    "正式版", "免安装", "镜像", "22h2", "23h2", "24h2", "25h2", "dmg",
)

_STAGE_WORDS = ("小学", "初中", "高中", "大学", "考研", "高等",
                "七年级", "八年级", "九年级", "中考", "高考")


def _clean_display_title(title: str) -> str:
    """展示用标题清理：去随机分享码和“资源名称：/标题：/名称：”前缀。"""
    t = _strip_random_suffix(str(title or ""))
    t = re.sub(r"^\s*(?:资源名称|标题|名称)\s*[:：]\s*", "", t)
    return t.strip()


# 去重时忽略的查询参数：
#   from/entry/ref… 是跟踪参数；pwd 是提取码，不是资源身份——
#   带/不带 pwd 的同一条分享应合并，提取码由 _password_from_url 补回。
# 网盘分享 ID 本身大小写敏感，不能整体小写。
_DEDUP_IGNORED_QUERY_KEYS = {
    "from", "entry", "ref", "referer", "spm", "scene", "channel",
    "invite", "timestamp", "pwd",
}


def _password_from_url(url: str) -> str:
    """从分享链接的 ?pwd= 参数提取码（很多 TG 帖不单独写密码）。"""
    m = re.search(r"[?&]pwd=([^&#]+)", str(url or ""), re.IGNORECASE)
    if not m:
        return ""
    try:
        from urllib.parse import unquote
        return unquote(m.group(1)).strip()
    except Exception:
        return m.group(1).strip()


def _canonical_url(u: str) -> str:
    """
    URL → 去重键。

    - 去掉协议/末尾斜杠/#片段；
    - 去掉 utm_*/from/entry/ref/pwd 等非身份参数
      （pwd 由 _password_from_url 单独保留）；
    - host 小写、去 www，阿里云盘新旧域名统一；
    - 磁力链接只认 xt（BTIH），不同 tracker 视为同一资源。
    """
    if not u:
        return u
    u = str(u).strip()
    if not u:
        return u
    if u.lower().startswith("magnet:"):
        _, _, query = u.partition("?")
        parts = [p for p in query.split("&") if p]
        xt = [p for p in parts if p.lower().startswith("xt=")]
        keep = xt or [p for p in parts
                      if not p.lower().startswith("tr=")]
        return "magnet:?" + "&".join(sorted(keep))
    u = re.sub(r"^[a-z]+://", "", u, flags=re.IGNORECASE)
    u = u.split("#", 1)[0]
    base, _, query = u.partition("?")
    base = re.sub(r"^www\.", "", base, flags=re.IGNORECASE)
    base = base.replace("aliyundrive.com/", "alipan.com/")
    # 只小写 host，路径保留原大小写（分享 ID 区分大小写）
    m = re.match(r"([^/]+)(/.*)?$", base)
    if m:
        base = m.group(1).lower() + (m.group(2) or "")
    base = base.rstrip("/")
    try:
        from urllib.parse import unquote
        base = unquote(base)
    except Exception:
        pass
    params = []
    for part in query.split("&"):
        if not part:
            continue
        key = part.split("=", 1)[0].lower()
        if key in _DEDUP_IGNORED_QUERY_KEYS or key.startswith("utm_"):
            continue
        params.append(part)
    return base + ("?" + "&".join(sorted(params)) if params else "")


def _dedup_and_rank(archive_items, realtime_items, keyword, click_map=None,
                    source_penalties=None):
    """
    融合历史索引与实时搜索结果：跨源去重 + 综合排序。

    规则：
      1. 以 URL 为标准去重（规范化：去协议/去末尾斜杠/解码）
      2. 历史与实时撞车时：保留实时结果（数据更新、带完整来源），
         但若历史结果带密码而实时不带，则补全密码
      3. 综合评分 = 相关度(标题/来源命中) + 精确/前缀 + 存活(实时优先)
                      + 网盘偏好 + 点击热度 + 来源质量降权
    返回 (完整融合排序列表, 历史计数, 实时计数)。
    截断交给上层的 _group_resources（按资源分组后再限量）。
    """
    click_map = click_map or {}

    def norm_url(u):
        return _canonical_url(u)

    def score(item):
        s = 0.0
        # 相关度：标题或来源/URL 命中关键词
        blob = " ".join([
            str(item.get("title", "") or ""),
            str(item.get("source", "") or ""),
            str(item.get("url", "") or ""),
        ]).lower()
        if keyword and keyword.lower() in blob:
            s += 3.0

        # ★ 精确/前缀标题优先；包含但不是开头（同人/衍生/长广告）轻降权。
        #   先去掉开头年份/标签/画质角标，避免“2024考研英语真题”被误判。
        #   长尾查询（带 4K/第七版/点读版 等修饰）额外用“核心词”做前缀命中，
        #   否则标题永远无法以完整长查询开头，排序只能靠子串。
        title_l = str(item.get("title") or "")
        raw_title_l = str(item.get("_title_raw") or title_l)
        title_key = _normalize_title_key(_clean_title_start(title_l))
        kw_key = _normalize_title_key(keyword)
        core_kw_key = _normalize_title_key(_core_query_text(keyword))
        match_key = ""
        if title_key and kw_key:
            if title_key == kw_key:
                s += 3.5
                match_key = kw_key
            elif title_key.startswith(kw_key):
                s += 1.5
                match_key = kw_key
            elif (core_kw_key and len(core_kw_key) >= 3
                  and core_kw_key != kw_key):
                if title_key == core_kw_key:
                    s += 3.0
                    match_key = core_kw_key
                elif title_key.startswith(core_kw_key):
                    s += 1.3
                    match_key = core_kw_key
        if match_key:
            extra = len(title_key) - len(match_key)
            if extra >= 8:
                s -= 1.5 if len(match_key) <= 4 else 0.8
            elif extra >= 4:
                s -= 1.0 if len(match_key) <= 4 else 0.5
            if len(match_key) <= 4:
                tail = title_key[len(match_key):]
                if tail.startswith(match_key):
                    s -= 1.5     # 活着活着…：重复标题，通常是同人/段子
                elif _SPINOFF_TAIL_RE.match(tail):
                    s -= 1.5     # 活着就是爱 / 狂飙之浴血玫瑰
                elif not (_EDITION_TAIL_RE.match(tail)
                          or (tail.endswith("版") and len(tail) <= 6)
                          or tail[:1].isdigit()):
                    s -= 0.5     # 活着回来的男人：同前缀不同作品
                if re.search(rf"{re.escape(keyword)}\s*[:：]", title_l):
                    s -= 0.8
        elif len(kw_key) <= 6 and kw_key and kw_key in title_key:
            s -= 1.0

        # ★ 营销/同人/解说类标题降权；查询本身包含该词时不降。
        kw_l = (keyword or "").lower()
        title_low = title_l.lower()
        marker_hits = sum(
            1 for m in _NEGATIVE_TITLE_MARKERS
            if m in title_low and m not in kw_l
        )
        if marker_hits:
            s -= min(2.0, 0.5 * marker_hits)
        marketing_hits = sum(
            1 for m in _MARKETING_MARKERS if m in title_low)
        if marketing_hits:
            s -= min(2.0, 0.4 * marketing_hits)

        # ★ 正片质量：合集/整季、4K/1080P、字幕加分；
        #   单集、枪版/预告/花絮降权。保证“全60集 4K”排在“S01E50”前面。
        #   用原始标题判断：展示标题可能已被截断，丢掉“更新至60集”。
        s += _quality_bonus(raw_title_l.lower())

        # ★ “安装包”查询下的“免安装中文版”多为游戏/绿色版，不是安装包；
        #   例如 Office Fight/Office Simulator 免安装中文版。
        if ("安装包" in kw_l and "免安装" in title_low
                and "安装包" not in title_low):
            s -= 3.0
        # ★ 非游戏查询降权明显是游戏资源的标题（单机/游戏/Build 版本号）。
        if ("游戏" not in kw_l and "单机" not in kw_l
                and ("单机" in title_low or "游戏" in title_low
                     or "build." in title_low)):
            s -= 1.0

        # ★ 查询类别与标题类别明显冲突时降权
        #   （Office 安装包 → Office Romance 电影；高等数学教材 → 小学教辅）。
        #   标题包含完整查询词时跳过，避免误伤同 IP 的影视/音乐衍生内容。
        qcat = classify_category(keyword)
        if keyword and kw_l not in title_low:
            tcat = classify_category(title_l)
            if (qcat in ("movie", "study", "software", "music")
                    and tcat in ("movie", "study", "software", "music")
                    and qcat != tcat):
                s -= 1.5
        # 软件查询优先可安装/可运行资源，而不是同名的教程书/管理器。
        if qcat == "software" and any(
                w in title_low for w in _SOFTWARE_ASSET_MARKERS):
            s += 0.8

        # ★ 学段冲突：搜“高等数学”时，小学/初中/高中教辅降权。
        q_stages = {w for w in _STAGE_WORDS if w in kw_l}
        t_stages = {w for w in _STAGE_WORDS if w in title_low}
        if q_stages and t_stages and not (q_stages & t_stages):
            s -= 0.6

        # ★ 有效性（智能检测状态机）：确认失效重罚、疑似降权、已验证轻奖。
        #   实时结果默认视为存活（validity='' 不奖不罚，由实时体检补充状态）。
        validity = item.get("validity") or (
            "dead" if item.get("alive", 1) == 0 else ""
        )
        if validity == "dead":
            s -= 8.0
        elif validity == "suspect":
            s -= 2.5
        elif validity == "ok":
            s += 0.5
        # 有密码的信息更完整，加分
        if item.get("password"):
            s += 1.0
        # 标题非空加分
        if item.get("title"):
            s += 0.5
        # 网盘类型偏好
        dt = str(item.get("disk_type", "") or "").lower()
        s += -0.05 * DISK_PRIORITY.get(dt, 10)
        # 时间新鲜度
        ts = str(item.get("datetime") or item.get("last_seen") or "")
        if ts:
            s += 0.3  # 有时间信息的略靠前
        # ★ 点击热度反馈：用户点过的资源 = 真实有效性投票。
        #   本关键词下点过的权重高，全局点过的略作加成；上限 2.5，
        #   保证不会盖过相关度差距过大的结果。
        #   已确认失效的链接永不加成——死链不配靠点击置顶。
        if validity != "dead":
            c = click_map.get(item.get("url") or "")
            if c:
                s += min(2.5, c.get("kw", 0) * 0.8 + c.get("total", 0) * 0.2)
        # ★ 来源质量降权：只对“已检测样本足够 + 高死链 + 低独家”的来源生效，
        #   在 score 上做小幅乘性/加性调整，不改变排序结构。
        if source_penalties:
            p = source_penalties.get(str(item.get("source") or ""), 1.0)
            if p < 1.0:
                if s > 0:
                    s *= p
                else:
                    s -= (1.0 - p) * 3.0
        return s

    merged = {}
    history = 0
    realtime = 0

    def upsert(item, is_realtime):
        key = norm_url(item.get("url", ""))
        if not key:
            return
        raw_title = str(item.get("title") or "")
        it = dict(item)
        # 原始标题单独保留：分组/季集/画质判断要用完整标题，
        # 而展示用的 title 会被 _shorten_title 截断（可能切掉“更新至60集”）。
        it["_title_raw"] = raw_title
        # 精简实时广告式标题；历史索引入库时已精简，这里冗余处理以保证一致
        if isinstance(it.get("title"), str):
            it["title"] = _shorten_title(_clean_display_title(it["title"]))
        # 很多分享把提取码放在 URL 的 ?pwd= 里，单独字段是空的；
        # 先补出来，去重合并时才不会把密码丢掉。
        if not it.get("password"):
            it["password"] = _password_from_url(it.get("url"))
        existing = merged.get(key)
        if existing is None:
            merged[key] = it
            return
        # 撞车处理：带/不带 pwd 的同一分享已归到同一 key，
        # 两边任意一边有提取码都保留下来。
        if not it.get("password") and existing.get("password"):
            it["password"] = existing["password"]
        if not existing.get("password") and it.get("password"):
            existing["password"] = it["password"]
        if is_realtime:
            # 实时更新 → 覆盖；缺密码则从历史补
            old_pw = existing.get("password") or ""
            new_item = it
            if not new_item.get("password") and old_pw:
                new_item["password"] = old_pw
            # ★ 实时覆盖不能丢掉已确认的智能检测结果：
            #   索引库早已判定 失效/疑似 的链接，不能因为实时又搜到就洗白
            for k in ("validity", "state_summary", "checked_at", "fail_cnt"):
                if not new_item.get(k) and existing.get(k):
                    new_item[k] = existing.get(k)
            merged[key] = new_item

    for it in archive_items:
        upsert(it, is_realtime=False)
    for it in realtime_items:
        upsert(it, is_realtime=True)

    ranked = sorted(
        (dict(v) for v in merged.values()),
        key=score, reverse=True,
    )
    history = sum(1 for r in ranked if r.get("from_archive"))
    realtime = sum(1 for r in ranked if not r.get("from_archive"))
    return ranked, history, realtime


# ==========================================
# 同资源聚合分组
# 同一资源往往被多个频道/插件以不同网盘、不同画质重复发布，
# 直接平铺会占满结果页。这里按"归一化标题"聚类：
#   主卡 = 该资源信息最完整/最可用的一条链接
#   variants = 同资源的其余链接（不同网盘/不同发布帖）
# ==========================================

# 归一化时剔除的画质/压制/包装类词——它们不区分资源本身
_QUALITY_TOKENS = {
    "4k", "8k", "2k", "hd", "sd", "uhd", "hdr", "hdr10", "dv", "atmos",
    "hevc", "avc", "remux", "webdl", "webrip", "bluray", "bd", "dts",
    "flac", "ape", "mp3", "mkv", "mp4", "iso", "60fps", "120fps",
    "蓝光", "高清", "超清", "原盘", "中字", "简繁", "简体", "繁体", "双语",
    "国英", "国语", "粤语", "英语", "中英", "官方", "多国", "字幕",
    "内封", "内嵌", "外挂", "杜比", "视界", "全景声", "珍藏", "收藏",
    "夸克", "夸克网盘", "百度网盘", "阿里云盘", "阿里网盘", "迅雷",
    "迅雷网盘", "网盘", "uc网盘", "115网盘", "彩色版", "双版", "彩版",
    "多音轨", "杜比视界", "纯净版", "完整版", "高码率", "sdr",
    # 发布/修复/剪辑状态词：同一年份下这些差异不构成“不同资源”，
    # 应作为同一张卡的变体，而不是平铺成多张重复卡。
    "未删减", "未删减版", "无删减", "删减版", "已刮销", "修复版",
    "重制版", "加长版", "导演剪辑版", "导演剪辑", "国配", "台配",
    "国配版", "台配版", "中字版", "字幕版", "双语版", "高码",
    # 常见发布描述：同片不同字幕/语轨/完整度，不应拆成多张卡
    "特效", "中文字幕", "硬字幕", "软字幕", "超高清", "完整",
    "全集", "完结", "国粤英三语", "国粤英", "三语",
    # 题材/番外/更新状态：同剧不同发布帖的包装词，不构成不同资源
    "动画", "动漫", "动谩", "剧版", "电影版", "动画版",
    "番外", "番外篇", "更新中", "连载", "连载中", "入库", "最新",
    # 裸集/话/期只在被标点切开后成为独立 token（如 “… /集 更22集”），
    # 属于包装噪声；“神话/诗集”这类词不会被切开，不受影响。
    "集", "话", "期",
}
_QUALITY_PAT = re.compile(
    r"^(?:h\.?26[45]|x26[45]|2160p|1080p|720p|480p|bd\d{3,4}p|"
    r"web-?dl|blu-?ray|\d+bit|全\d+集|\d+集全|更新至\d+集|"
    r"国.*音轨|.*字幕|.*双语|.*中英|.*台粤|"
    r"\d+(?:\.\d+)?(?:gb|mb|tb)|sdr)$",
    re.IGNORECASE,
)
# 与中文粘连的画质/语轨词（UHD原盘、国语配音、蓝光4K）无法靠整词匹配
# 剔除，分组键再做一次保守的子串清理。按长度降序，先删长词避免残留。
_KEY_NOISE_SUBSTRINGS = tuple(sorted({
    "uhd", "hdr10", "hdr", "remux", "bluray", "blu", "webdl", "webrip",
    "hevc", "x265", "x264", "h264", "h265", "avc", "2160p", "1080p",
    "720p", "480p", "60fps", "120fps", "dts", "flac", "ape", "mp3",
    "mkv", "mp4", "dvd", "iso", "4k", "8k", "2k", "hd", "sdr", "dv",
    "蓝光", "高清", "超清", "原盘", "中字", "简繁", "简体", "繁体",
    "双语", "国英", "国语", "粤语", "中英", "内封", "内嵌", "外挂",
    "杜比", "视界", "全景声", "多音轨", "中文字幕", "硬字幕", "软字幕",
    "未删减版", "未删减", "无删减", "删减版", "已刮销", "修复版",
    "重制版", "加长版", "导演剪辑版", "导演剪辑", "国配", "台配",
    "国配版", "台配版", "中字版", "字幕版", "双语版", "国语配音",
    "粤语配音", "国英双音", "双音", "番外篇", "番外", "动谩",
    "剧版", "电影版", "动画版", "更新中", "连载中", "最新",
}, key=len, reverse=True))

# 注意：季数（第X季/S01）不剔除——季与季是不同资源，不能合并

_RANDOM_SUFFIX_RE = re.compile(r"[_\-\s]+([A-Za-z0-9]{5,12})$")
_SEASON_LIKE_RE = re.compile(
    r"(?i)^(?:s\d+(?:e\d+)?|e\d+|ep\d+|v\d+|第.+季|全\d+集|\d{4})$")


def _strip_random_suffix(text: str) -> str:
    """去掉标题末尾的随机分享码（如 _8RoUmhV/_abwST），保留年份/季集。"""
    t = str(text or "")
    m = _RANDOM_SUFFIX_RE.search(t)
    if not m:
        return t
    suffix = m.group(1)
    if _SEASON_LIKE_RE.fullmatch(suffix):
        return t
    has_alpha = any(c.isalpha() for c in suffix)
    has_digit = any(c.isdigit() for c in suffix)
    has_upper = any(c.isupper() for c in suffix)
    has_lower = any(c.islower() for c in suffix)
    # 随机分享码通常“字母+数字”混排，或大小写混排；
    # 普通英文单词（全小写/全大写）不剥。
    if (has_alpha and has_digit) or (has_upper and has_lower):
        return t[:m.start()].rstrip("_- ")
    return t


# ==========================================
# 季集识别：同一部剧的不同单集应聚合为一张卡，
# 合集/整季帖优先当主卡；不同季不能合并。
# ==========================================
_CN_NUM_CHARS = "一二三四五六七八九十百零〇两"
_CN_NUM_MAP = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
# 单集：S01E05 / EP05 / 第5集 / 第五话 / 第3期
# 不识别裸 E05：避免把 CPU 型号（E5-2670）之类非影视标题误判成单集。
_EP_SINGLE_RE = re.compile(
    r"(?i)(?:s\d{1,2}\s*e\d{1,3}|ep\s*\d{1,3}|"
    rf"第\s*[{_CN_NUM_CHARS}\d]{{1,3}}\s*[集话期])")
# 合集/整季/更新包：全60集 / 60集全 / 更新至60集 /
# S01E01-E60 / 第1-60集 / 1-60集 / 全集 / 合集 / 完结
_EP_PACK_RE = re.compile(
    r"(?i)(?:"
    rf"全\s*[{_CN_NUM_CHARS}\d]{{1,3}}\s*[集话期]|"
    rf"[{_CN_NUM_CHARS}\d]{{1,3}}\s*[集话期]\s*全|"
    rf"共\s*[{_CN_NUM_CHARS}\d]{{1,3}}\s*[集话期]|"
    rf"更(?:新)?(?:至)?\s*[{_CN_NUM_CHARS}\d]{{1,3}}(?:\s*[集话期])?|"
    r"s\d{1,2}\s*e\d{1,3}\s*[-~至]\s*e?\d{1,3}|"
    rf"第\s*[{_CN_NUM_CHARS}\d]{{1,3}}\s*[-~至]\s*"
    rf"[{_CN_NUM_CHARS}\d]{{1,3}}\s*[集话期]|"
    rf"[{_CN_NUM_CHARS}\d]{{1,3}}\s*[-~至]\s*"
    rf"[{_CN_NUM_CHARS}\d]{{1,3}}\s*[集话期]|"
    r"全集|合集|完结)")
# 季：S02 / Season 2 / 第二季（第一季归一为“无季”，与不带季的合集合并）
_SEASON_RE = re.compile(
    r"(?i)(?:s(\d{1,2})|season\s*(\d{1,2})|"
    rf"第\s*([{_CN_NUM_CHARS}\d]{{1,3}})\s*季)")


def _cn_num(value) -> int:
    """中文数字/阿拉伯数字 → int（只覆盖季集常见的 1-99）。"""
    v = str(value or "").strip()
    if not v:
        return 0
    if v.isdigit():
        return int(v)
    if "十" in v:
        head, _, tail = v.partition("十")
        tens = _CN_NUM_MAP.get(head, 1) if head else 1
        ones = _CN_NUM_MAP.get(tail, 0) if tail else 0
        return tens * 10 + ones
    return _CN_NUM_MAP.get(v, 0)


def _extract_season_number(title: str) -> int:
    m = _SEASON_RE.search(str(title or ""))
    if not m:
        return 0
    return _cn_num(m.group(1) or m.group(2) or m.group(3))


def _episode_kind(title: str) -> str:
    """'pack'（合集/整季/更新包） / 'single'（单集） / ''。"""
    t = str(title or "")
    if not t:
        return ""
    if _EP_PACK_RE.search(t):
        return "pack"
    if _EP_SINGLE_RE.search(t):
        return "single"
    return ""


def _series_display_title(title: str) -> str:
    """
    单集标题 → 干净剧名（没有合集帖时，聚合卡的标题用它）。

    “喜羊羊… S02E50” → “喜羊羊… 第2季”
    “庆余年 第二季 第5集” → “庆余年 第二季”
    """
    season = _extract_season_number(title)
    t = _EP_PACK_RE.sub(" ", str(title or ""))
    t = _EP_SINGLE_RE.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip(" -_·:：,，")
    if season >= 2:
        t = f"{t} 第{season}季".strip()
    return _clean_display_title(t)


# 低质量/非正片标记：搜影视时排到后面
_LOW_QUALITY_MARKERS = (
    "枪版", "抢先版", "预告片", "预告", "花絮", "速看", "切片",
    "片段", "泄露版", "盗摄", "TC版", "TS版",
)


def _quality_bonus(title: str) -> float:
    """正片质量加分：合集/4K/1080P/字幕；单集/枪版/预告降权。"""
    t = str(title or "").lower()
    s = 0.0
    kind = _episode_kind(t)
    if kind == "pack":
        s += 2.0
    elif kind == "single":
        s -= 1.0
    if re.search(r"(?:4k|2160p|uhd)", t):
        s += 1.0
    elif re.search(r"(?:1080p|蓝光|blu-?ray|remux|原盘)", t):
        s += 0.6
    elif re.search(r"(?:720p|高清|hd)", t):
        s += 0.2
    if re.search(r"(?:中字|中英|简繁|内封|内嵌|双语|国语|粤语)", t):
        s += 0.3
    low_hits = sum(1 for m in _LOW_QUALITY_MARKERS if m in t)
    if low_hits:
        s -= min(2.0, 0.8 * low_hits)
    return s


def _diversity_title_key(title: str) -> str:
    """
    首页“同标题簇”占位用的键。

    复用 _normalize_title_key（去随机后缀/画质词/发布状态词），但保留年份
    和季集：不同年份/不同季集是不同资源，不能被多样性规则强行拆散。
    """
    return _normalize_title_key(_clean_title_labels(title))


def _normalize_title_key(title: str) -> str:
    """
    标题 → 归一化分组键：去随机后缀/标点/画质词，仅保留字母/数字/汉字。

    季集处理：
      - 同一季的不同单集（S01E05 / 第5集）归一成同一个键，聚合为一张卡；
      - 第一季与“不带季”的合集视为同一资源（多数剧只有一季）；
      - 第二季及以后保留 sN，避免把不同季合并。
    """
    season = _extract_season_number(title)
    t = str(title or "")
    # 先删合集/单集标记，再删季标记；季号单独作为后缀拼回，
    # 避免“S01E05”被拆成 “s1 e5” 后单集标记认不出来。
    t = _EP_PACK_RE.sub(" ", t)
    t = _EP_SINGLE_RE.sub(" ", t)
    t = _SEASON_RE.sub(" ", t)
    t = _strip_random_suffix(t)
    t = t.lower()
    # 单集体积（1.4GB/集、67.6G）先整体去掉；否则 "1.4GB/集" 会留下
    # 一个 "集"，让同一部剧的 4K 发布帖被分到不同组。
    t = re.sub(
        r"\d+(?:\.\d+)?\s*(?:gb|g|mb|m|tb|t)\s*(?:(?:/|每)\s*)?[集话期]",
        " ", t, flags=re.IGNORECASE)
    # 其余文件大小（19.69GB/500MB/67.6G）不是资源身份的一部分
    t = re.sub(r"\d+(?:\.\d+)?\s*(?:gb|g|mb|m|tb|t)\b", " ", t,
               flags=re.IGNORECASE)
    # 结尾的题材/番外/更新状态标签不构成资源身份
    t = re.sub(
        r"(?:动画|动漫|动谩|剧版|电影版|动画版|番外篇|番外|"
        r"更新中|连载中)\s*$", " ", t)
    t = re.sub(
        r"[\[\]【】()（）{}<>《》「」『』|/\\·:：;；,，。.?？!！~～\"'“”‘’"
        r"\-…—–]", " ", t)
    tokens = t.split()
    keep = [tok for tok in tokens
            if tok not in _QUALITY_TOKENS and not _QUALITY_PAT.match(tok)]
    # 中文标题后的英文原名/译名（肖申克的救赎 The Shawshank Redemption）
    # 不是资源身份：首段 CJK >=4 字时丢弃其后的纯字母词，保留年份/数字。
    # 首段太短（活着/三体/庆余年）时不丢，避免把“活着 VIVANT”并进原著。
    if keep and len(keep[0]) == 1 and keep[0].isascii() and keep[0].isalpha():
        keep = keep[1:]
    if keep and _has_cjk(keep[0]) and len(keep[0]) >= 4:
        keep = [
            tok for tok in keep
            if tok.isdigit() or not (tok.isascii() and tok.isalpha())
        ]
    key = "".join(keep)
    for noise in _KEY_NOISE_SUBSTRINGS:
        if noise in key:
            key = key.replace(noise, "")
    key = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", key)
    if season >= 2 and key:
        key = f"{key}s{season}"
    return key


def _rep_score(it: dict, source_penalties=None) -> float:
    """
    组内选主卡：有效 > 直链 > 有密码 > 来源质量 > 网盘偏好。

    用大间隔保证“已验证有效”永远压过“未检测但信息更全”的链接：
    否则一个带密码的未检测直链会把已验证的学霸盘详情页挤下主卡。
    """
    s = 0.0
    v = it.get("validity") or ""
    dead = v == "dead" or it.get("alive", 1) == 0
    if v == "ok":
        s += 20.0
    elif dead:
        s -= 30.0
    elif v == "suspect":
        s -= 10.0
    else:
        s += 10.0  # 未检测：可当主卡，但低于已验证有效

    # ★ 合集/整季帖优先当主卡：+12 足以压过“已验证的单集”（20-3=17），
    #   但压不过“已验证的合集”（32）；已确认失效的合集仍然出局。
    #   没有合集时，单集里选信息最全的一条当主卡。
    ep_kind = _episode_kind(
        str(it.get("_title_raw") or it.get("title") or ""))
    if ep_kind == "pack":
        s += 12.0
    elif ep_kind == "single":
        s -= 3.0

    # 直链优先：学霸盘详情页要二跳，同组有直链时不让它当主卡
    url = str(it.get("url") or "")
    if "xuebapan.com/info/" in url:
        s -= 3.0
    elif re.match(
        r"^https?://(?:[^/]+\.)?(?:quark\.cn|baidu\.com|alipan\.com|"
        r"aliyundrive\.com|xunlei\.com|115\.com|115cdn\.com|"
        r"123pan\.com|123684\.com|123912\.com|123865\.com|189\.cn|"
        r"uc\.cn|mypikpak\.com|guangyapan\.com)/",
        url,
    ):
        s += 3.0

    if it.get("password"):
        s += 1.2
    if source_penalties:
        p = source_penalties.get(str(it.get("source") or ""), 1.0)
        if p < 1.0:
            s -= 0.6
    if not it.get("from_archive"):
        s += 0.3
    s += min(len(str(it.get("title") or "")), 60) / 300.0
    s -= 0.1 * DISK_PRIORITY.get(str(it.get("disk_type") or "").lower(), 10)
    return s


_VARIANT_FIELDS = ("url", "password", "disk_type", "source",
                   "datetime", "last_seen", "alive", "from_archive", "title",
                   "validity", "state_summary", "checked_at", "msg_image")


_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")


def _group_resources(ranked: list, limit: int = 60,
                     source_penalties=None, keyword=None) -> list:
    """
    把融合排序后的平铺链接聚合成"资源卡片"列表。

    - 归一化标题一致（键长>=3；2 字 CJK 短名如三体/活着允许按主体
      合并，泛词电影/软件/教程除外）→ 同组
    - 年份兼容：同一标题主体下，只有一个年份时，无年份/带年份合并；
      出现多个年份时按年份拆开（三体不同版本不合并；1980-2025 这种
      多年份范围单独成组）。
    - 无标题或键过短的条目按 URL 独立成卡，不参与聚合
    - 每组产出一张主卡：保留主链接全部字段，其余链接进 variants
    - 组间顺序沿用原相关性排序（组内重选主卡不影响组序）
    """
    groups = {}
    min_index = {}
    base_groups = {}

    def add(gk, idx, item):
        if gk not in groups:
            groups[gk] = []
            min_index[gk] = idx
        groups[gk].append(item)

    for idx, it in enumerate(ranked):
        # 原始标题（未截断）用于分组；展示标题可能被截掉“更新至60集”
        raw_title = str(it.get("_title_raw") or it.get("title") or "")
        group_title = _clean_title_labels(raw_title)
        # ★ 去掉片名前的发布者/频道标签：【库库光影】【汐汐精选】、
        #   “📺 剧名：”等。用当前查询词定位正文起点，比维护发布者名单可靠。
        if keyword:
            kw_l = str(keyword).strip().lower()
            # 短查询（三体/活着）不做前缀裁剪：否则“我的三体”会被并进“三体”。
            # 长片名（>=6 字）才用查询词定位正文起点，去掉发布者/频道标签。
            pos = group_title.lower().find(kw_l) if len(kw_l) >= 6 else -1
            if pos > 0:
                group_title = group_title[pos:]
        full = _normalize_title_key(group_title)
        if not full:
            add(("u", str(it.get("url", ""))), idx, it)
            continue
        years = set(_YEAR_RE.findall(full))
        if len(years) >= 2:
            # 多年份/年份范围（如 1980-2025 合集）：不与任何年份合并
            add(("t", full), idx, it)
            continue
        year = next(iter(years)) if years else ""
        base = _YEAR_RE.sub("", full) if years else full
        # 2 字 CJK 短名（三体/活着/狂飙）：允许和“带年份的同名版本”合并；
        # 泛词（电影/软件/教程）仍按 URL 独立，避免把所有资源揉成一张卡。
        short_ok = (len(base) >= 2 and _has_cjk(base)
                    and base not in _GENERIC_SHORT_QUERIES)
        if len(base) < 3 and not short_ok:
            if (full and len(full) >= 2 and _has_cjk(full)
                    and full not in _GENERIC_SHORT_QUERIES):
                add(("s", full), idx, it)
            else:
                add(("u", str(it.get("url", ""))), idx, it)
            continue
        base_groups.setdefault(base, []).append((idx, it, year))

    for base, members in base_groups.items():
        distinct_years = {y for _, _, y in members if y}
        if len(distinct_years) <= 1:
            # 只有一个年份（或没有年份）：无年份标题并入同组
            for idx, it, _ in members:
                add(("t", base), idx, it)
        else:
            # 多个年份：按年份拆开；无年份的单独成组，避免误挂到某一年
            for idx, it, year in members:
                add(("ty", base, year or "unknown"), idx, it)

    order = sorted(groups.keys(), key=lambda k: min_index[k])
    cards = []
    for gk in order:
        members = groups[gk]
        if len(members) == 1:
            single = dict(members[0])
            single.pop("_title_raw", None)
            cards.append(single)
            continue
        rep = max(members,
                  key=lambda m: _rep_score(m, source_penalties))
        card = dict(rep)
        # 主链没有消息封面时，从任一带图的同资源成员继承
        if not card.get("msg_image"):
            card["msg_image"] = next(
                (m.get("msg_image") for m in members if m.get("msg_image")), "")
        card["variants"] = [
            {k: m.get(k) for k in _VARIANT_FIELDS}
            for m in members if m is not rep
        ]
        card["variant_count"] = len(card["variants"])
        # ★ 同一部剧的单集/合集已按归一化键聚到一组：
        #   有合集帖 → 合集标题当主卡标题（主链接仍可保留更可靠的单集）；
        #   没有合集 → 用干净剧名 + “单集可选”，避免主卡标题显示成 S01E50。
        ep_kinds = [
            _episode_kind(str(m.get("_title_raw") or m.get("title") or ""))
            for m in members
        ]
        episode_count = sum(1 for k in ep_kinds if k == "single")
        if episode_count:
            card["episode_count"] = episode_count
            card["has_pack"] = any(k == "pack" for k in ep_kinds)
            if card["has_pack"]:
                pack_member = next(
                    (m for m, k in zip(members, ep_kinds) if k == "pack"), None)
                if pack_member is not None:
                    card["title"] = pack_member.get("title") or card["title"]
            else:
                base_title = _series_display_title(
                    str(rep.get("_title_raw") or rep.get("title") or ""))
                if base_title:
                    card["title"] = f"{base_title}（单集可选）"
        card.pop("_title_raw", None)
        cards.append(card)
    return cards if limit is None else cards[:limit]


def _card_validity(card: dict) -> str:
    """
    资源卡整体有效性（含同资源变体链接）：
    任一链接已验证有效 → ok；全部确认失效 → dead；
    有疑似且无已验证 → suspect；其余（未检测/不支持检测）→ ''。
    """
    members = [card] + list(card.get("variants") or [])
    vs = [str(m.get("validity") or "") for m in members]
    if any(v == "ok" for v in vs):
        return "ok"
    if all(v == "dead" for v in vs):
        return "dead"
    if any(v == "suspect" for v in vs):
        return "suspect"
    return ""


# ==========================================
# 点击后智能体检
# 用户点开链接 = 一次"我要用它"的动作。若分享已失效，
# 必须尽快打回：点击跳转不受影响，后台线程立即走智能检测
# 状态机（首次失败→疑似，连续确认→失效），检出失效 →
# 打标 + 撤销点击热度 + 失效该关键词的结果缓存。
# ==========================================
_checking_urls = set()
_checking_lock = threading.Lock()


def _invalidate_kw_cache(kw: str):
    """失效某关键词的全部搜索结果缓存（排序/隐藏状态变化时调用）"""
    kw = (kw or "").strip().lower()
    if not kw:
        return
    # 缓存键格式：v2|<kw>|<archive/realtime>|<src>
    needle = "|" + kw.lower() + "|"
    with _search_cache_lock:
        for k in [k for k in _search_cache if needle in k]:
            _search_cache.pop(k, None)


# ==========================================
# 资源封面：标题 → 豆瓣海报匹配
# ==========================================

def _hot_ok(title: str) -> bool:
    """热搜词降噪：过滤带 URL/@/超长的标题，保证搜索页 chips 观感"""
    t = (title or "").strip()
    if not t or len(t) > 28:
        return False
    if re.search(r"https?:|www\.|t\.me/|@", t, re.IGNORECASE):
        return False
    return True


def _poster_query(title: str) -> str:
    """
    资源标题 → 豆瓣建议搜索的查询串。
    资源标题常带【合集】【全36集】【4K】等包装段落堆砌，截到首段并去掉
    画质/集数噪声，剩下的"剧名+年份"才是豆瓣能匹配的实体词。
    """
    t = (title or "").strip()
    if not t:
        return ""
    # 以【】/《》开头的标题取第一个括号内的内容；否则截到第一个包装段落/分隔符
    m = re.match(r"^[【\[《「]\s*(.+?)\s*[】\]》」]", t)
    if m:
        t = m.group(1).strip()
    else:
        t = re.split(r"[【\[（(«《「]|·| - | \| |💾|📁|💬", t)[0].strip() or t
    # 去画质/集数/季数噪声
    t = re.sub(
        r"(全\d+集|\d+集全|更新至\d+集|第?\d+\s*[季期]|4k|8k|1080p|720p|"
        r"hdr|蓝光|高清|超清|hq|中字|国语|粤语)",
        " ", t, flags=re.IGNORECASE,
    )
    t = re.sub(r"\s+", " ", t).strip(" ·-:：,，")
    return t[:24]


def _poster_variants(query: str) -> list:
    """
    豆瓣匹配降级变体链：完整清洗词 → 去通用包装后缀（合集/全集/教程...）→ 首词。
    逐个尝试直到命中，最大化封面命中率且不引入乱匹配。
    """
    q = (query or "").strip()
    if not q:
        return []
    variants = [q]
    stripped = re.sub(
        r"(合集|全集|资源|完整版|正版|网盘|片源|电影|电视剧|综艺|动漫|"
        r"纪录片|教程|课程|附赠|合集版)$", "", q,
    ).strip(" ·-:：,，")
    if stripped and stripped not in variants:
        variants.append(stripped)
    # 去年份/备注括号：庆余年（2019） → 庆余年
    noparen = re.sub(r"[（(][^）)]*[）)]", "", stripped).strip(" ·-:：,，")
    if noparen and noparen not in variants:
        variants.append(noparen)
    if " " in noparen:
        first = noparen.split(" ")[0].strip()
        if len(first) >= 2 and first not in variants:
            variants.append(first)
    return variants


def _async_check_link(checker, index_db, url, disk_type, password, kw=""):
    """后台智能体检单条链接；确认失效则打标并撤销热度。同 URL 去重防重复体检。"""
    def _work():
        try:
            res = checker.check_items([
                {"url": url, "disk_type": disk_type or "",
                 "password": password or ""}
            ])
            info = res.get(url) or {}
            validity = info.get("validity") or ""
            if validity == "dead":
                heat = index_db.clear_click_heat(url)
                _invalidate_kw_cache(kw)
                logger.info(f"[智能检测] 点击体检确认失效: {url[:60]} "
                            f"(撤销热度 {heat} 条)")
            else:
                state = info.get("state", "unknown")
                logger.info(f"[智能检测] 点击体检: {url[:60]} → "
                            f"{validity or state}")
        except Exception as e:
            logger.error(f"[智能检测] 点击体检 {url[:60]} 失败: {e}")
        finally:
            with _checking_lock:
                _checking_urls.discard(url)

    with _checking_lock:
        if url in _checking_urls:
            return
        _checking_urls.add(url)
    threading.Thread(target=_work, daemon=True).start()


def _background_smart_check(checker, cards: list, keyword: str):
    """
    搜索后静默补检：对结果池前排卡片的主链接里"未检测/已过期"的，
    后台送智能检测并刷新状态机；有新结论才失效该关键词缓存，
    下次搜索/翻页即可看到最新的降权与隐藏效果。
    """
    items = []
    for c in cards:
        u = str(c.get("url") or "")
        if not u or not re.match(r"^https?://", u, re.IGNORECASE):
            continue
        items.append({
            "url": u,
            "disk_type": str(c.get("disk_type") or "").strip(),
            "password": str(c.get("password") or "").strip(),
        })
        if len(items) >= Config.SMART_CHECK_BACKGROUND_LIMIT:
            break
    if not items:
        return

    def _work():
        try:
            res = checker.check_items(items)
            updated = any(
                (not v.get("fresh"))
                and v.get("state") not in ("unknown", "unsupported", "cached")
                for v in res.values()
            )
            if updated:
                # ★ 不再作废关键词缓存：搜索刚结束时补检几乎必然有新结论，
                #   一作废用户随后点网盘筛选就缓存未命中，又要跑整条管线
                #   （表现为"点百度还要重新搜"）。结论已写入索引库，前端
                #   check/batch 徽章会即时刷新，缓存留到 TTL 自然过期即可。
                logger.info(f"[智能检测] '{keyword}' 搜索后补检 {len(items)} 条，"
                            f"新结论已写入索引")
        except Exception as e:
            logger.error(f"[智能检测] 搜索后补检失败: {e}")

    threading.Thread(target=_work, daemon=True).start()


def register_routes(app, searcher, index_db, checker, analyzer,
                    pansou_client, douban_client=None):
    """注册所有路由（由 app.py 调用注入依赖）"""

    # ==========================================
    # 封面单飞解析 + 后台预热
    # 相同查询词全局只解析一次（in-flight 去重）；搜索后把前排卡片
    # 标题投入预热队列，后台 worker 持续填充 poster_cache：
    # 热门资源第二次搜索秒出图，第一次搜索由 poster/batch 的
    # 内联等待（2.5s）兜底。熔断期间 worker 自动暂停不污染缓存。
    # ==========================================
    poster_pool = ThreadPoolExecutor(max_workers=6)
    poster_inflight = {}
    poster_inflight_lock = threading.Lock()
    warm_queue = queue.Queue(maxsize=800)
    _warm_state = {"seen": set()}

    def _resolve_key(key):
        """解析单个查询词的封面并落缓存；熔断期返回 None 且不落库"""
        if douban_client.cooldown_active():
            return None
        img = None
        for q in _poster_variants(key):
            img = douban_client.suggest_poster(q)
            if img:
                break
        if img is None and douban_client.cooldown_active():
            return None  # 解析中途进入熔断：空结果不可信，不落缓存
        index_db.save_poster(key, img or "")
        return img

    def _submit_keys(keys):
        """提交解析任务（全局去重），返回 {key: Future}；顺带清理已完成的旧任务"""
        with poster_inflight_lock:
            for k in [k for k, f in poster_inflight.items() if f.done()]:
                poster_inflight.pop(k, None)
            futs = {}
            for k in keys:
                if not k:
                    continue
                if k not in poster_inflight:
                    poster_inflight[k] = poster_pool.submit(_resolve_key, k)
                futs[k] = poster_inflight[k]
            return futs

    def _wait_futures(futs, wait: float):
        """等待解析结果；超时/异常返回 None（调用方按无匹配处理，缓存已由 worker 落）"""
        out = {}
        deadline = time.time() + wait
        for k, f in futs.items():
            try:
                out[k] = f.result(timeout=max(0.05, deadline - time.time()))
            except Exception:
                out[k] = None
        with poster_inflight_lock:
            for k, f in futs.items():
                if poster_inflight.get(k) is f and f.done():
                    poster_inflight.pop(k, None)
        return out

    def _enqueue_warm(keys):
        """投递预热队列（去重；集合超 2000 清一次，重复请求由缓存兜底）"""
        for k in keys:
            if not k:
                continue
            with poster_inflight_lock:
                seen = _warm_state["seen"]
                if k in seen:
                    continue
                seen.add(k)
                if len(seen) > 2000:
                    seen.clear()
            try:
                warm_queue.put_nowait(k)
            except queue.Full:
                break

    def _warm_worker():
        while True:
            key = warm_queue.get()
            try:
                if key in index_db.get_posters([key]):
                    continue  # 缓存仍新鲜
                while douban_client.cooldown_active():
                    time.sleep(15)  # 豆瓣限流期挂起，恢复后继续
                _wait_futures(_submit_keys([key]), wait=60)
            except Exception as e:
                logger.error(f"[封面预热] {key[:30]!r} 失败: {e}")
            finally:
                warm_queue.task_done()

    if not _warm_state.get("started"):
        _warm_state["started"] = True
        threading.Thread(target=_warm_worker, daemon=True,
                         name="poster-warmer").start()
        try:
            _enqueue_warm(_poster_query(h["keyword"])
                          for h in index_db.get_history(15))
        except Exception:
            pass

    # ==========================================
    # 搜索管线（经典 /search 与流式 /search/stream 共用）
    #
    # 流式设计：同一条管线在两个入口下产生完全一致的结果与缓存——
    #   * 经典路径 progress=None：波次内不轮询，一次性等全部结束（与旧实现等价）；
    #   * 流式路径 progress=回调：波次内每 STREAM_POLL_SECONDS 轮询一次线程
    #     完成度，有新结果就增量吸收并推送中间帧（本地索引帧毫秒级先行）。
    # ==========================================

    def _parse_disk_types(raw):
        """网盘筛选兼容数组/逗号字符串；空值表示“全部”。"""
        if isinstance(raw, str):
            raw = [t.strip() for t in raw.split(",") if t.strip()]
        if isinstance(raw, list):
            return [str(t).strip().lower() for t in raw if str(t).strip()]
        return []

    def _hide_dead_cards(cards):
        """按配置拆分 可见/已确认失效 资源卡（保持原顺序）"""
        if not Config.HIDE_DEAD_LINKS:
            return cards, []
        visible, hidden = [], []
        for c in cards:
            if _card_validity(c) == "dead":
                hidden.append(c)
            else:
                visible.append(c)
        return visible, hidden

    def _fusion_payload(kw, archive_items, realtime_items, click_map,
                        pansou_error=None, source_penalties=None):
        """
        当前已吸收的结果 → 中间帧 payload（与最终 payload 同构，仅供流式
        渐进渲染；不写索引、不落缓存）。无可展示卡片时返回 None。
        """
        try:
            snapshot_real = [dict(ln) for ln in realtime_items]
            # 已知有效性状态提前带上：让中间帧与最终帧的排序尽量一致，减少跳动
            try:
                _vmap = index_db.get_validity_map(
                    [ln.get("url") for ln in snapshot_real])
                for ln in snapshot_real:
                    vi = _vmap.get(ln.get("url"))
                    if vi:
                        ln["validity"] = vi["validity"]
                        ln["state_summary"] = vi["state_summary"]
                        ln["checked_at"] = vi["checked_at"]
            except Exception:
                pass
            ranked_pool, hist_n, real_n = _dedup_and_rank(
                archive_items, snapshot_real, kw, click_map=click_map,
                source_penalties=source_penalties)
            cards = _group_resources(
                ranked_pool, limit=None, source_penalties=source_penalties,
                keyword=kw)
            visible, hidden = _hide_dead_cards(cards)
            if not visible:
                return None
            disk_counts = {}
            for c in visible:
                dt = str(c.get("disk_type") or "").strip().lower() or "others"
                disk_counts[dt] = disk_counts.get(dt, 0) + 1
            return {
                "cards": visible,
                "disk_counts": disk_counts,
                "raw_count": len(ranked_pool),
                "hist": hist_n,
                "real": real_n,
                "archive_raw": len(archive_items),
                "realtime_raw": len(snapshot_real),
                "hidden_dead": hidden[:50],
                "hidden_dead_count": len(hidden),
                "error": pansou_error,
            }
        except Exception as e:
            logger.error(f"构建流式中间帧失败: {e}")
            return None

    def _slice_response(payload, keyword, disk_types, sort, page, page_size,
                        correction, from_cache, t0, log_impressions=False):
        """完整结果池 → 请求层切片响应（网盘筛选 / 排序 / 分页），经典与流式共用。"""
        full_cards = payload["cards"]
        full_counts = payload.get("disk_counts") or {}
        raw_count = payload["raw_count"]
        hist_n = payload["hist"]
        real_n = payload["real"]
        archive_raw = payload["archive_raw"]
        realtime_raw = payload["realtime_raw"]
        pansou_error = payload["error"]

        def _disk_of(card):
            return str(card.get("disk_type") or "").strip().lower() or "others"

        # ====== 筛选（基于完整池）======
        selected = set(disk_types)
        cards = full_cards
        if selected:
            cards = [c for c in full_cards if _disk_of(c) in selected]

        # ====== 排序（必须在分页之前）======
        # relevance 完全沿用 _dedup_and_rank 的原顺序；time/name 只改展示顺序。
        def _time_key(card):
            raw = str(card.get("datetime") or card.get("last_seen") or "")
            if not raw or raw.startswith("0001"):
                return 0.0
            try:
                # 兼容 "Z" 与 "+08:00"；naive 时间按服务器本地时区换算。
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                return dt.timestamp()
            except (TypeError, ValueError):
                return 0.0

        if sort == "time":
            cards = sorted(
                cards,
                key=lambda c: (
                    _time_key(c),
                    str(c.get("title") or ""),
                    str(c.get("url") or ""),
                ),
                reverse=True,
            )
        elif sort == "name":
            cards = sorted(
                cards,
                key=lambda c: (
                    str(c.get("title") or "").casefold(),
                    str(c.get("url") or ""),
                ),
            )

        # ====== 首页来源多样性：单一来源最多占 cap 张，超出顺延 ======
        # 只作用于默认相关性排序的第 1 页；时间/名称排序尊重用户选择。
        if sort == "relevance" and page == 1 and Config.SOURCE_DIVERSITY_CAP > 0:
            cap = Config.SOURCE_DIVERSITY_CAP

            def _card_source(card):
                src = str(card.get("source") or "").strip()
                if not src:
                    for variant in (card.get("variants") or []):
                        src = str((variant or {}).get("source") or "").strip()
                        if src:
                            break
                return src

            primary, overflow, counts = [], [], {}
            for card in cards:
                src = _card_source(card)
                if counts.get(src, 0) < cap:
                    primary.append(card)
                    counts[src] = counts.get(src, 0) + 1
                else:
                    overflow.append(card)
            cards = primary + overflow

        # ====== 首页标题簇占位：同一资源不同发布帖最多占 cap 张 ======
        # 与上面的来源上限叠加：既防单来源刷屏，也防多个来源发同一个资源刷屏。
        # 只作用于默认相关性排序的第 1 页；不同年份/季集仍是不同簇。
        if sort == "relevance" and page == 1 and Config.TITLE_CLUSTER_CAP > 0:
            cap = Config.TITLE_CLUSTER_CAP
            primary, overflow, counts = [], [], {}
            for card in cards:
                ck = _diversity_title_key(card.get("title") or "")
                if not ck:
                    primary.append(card)
                    continue
                if counts.get(ck, 0) < cap:
                    primary.append(card)
                    counts[ck] = counts.get(ck, 0) + 1
                else:
                    overflow.append(card)
            cards = primary + overflow

        hidden_dead_cards = payload.get("hidden_dead") or []
        if selected:
            hidden_dead_cards = [c for c in hidden_dead_cards
                                 if _disk_of(c) in selected]

        disk_type_count = len({_disk_of(c) for c in cards})

        # ====== 分页切片（缓存命中与新搜索共用）======
        total_cards = len(cards)
        total_pages = max(1, (total_cards + page_size - 1) // page_size)
        if page > total_pages:
            page = total_pages
        start = (page - 1) * page_size
        page_cards = cards[start:start + page_size]

        # ====== 来源曝光：只在“真正出页”时记录一次 ======
        # 流式中间帧不记（会被完成帧覆盖）；经典 /search 与流式 done 帧各记一次。
        # 按卡片计：同资源卡内的多网盘变体只算一次，来源取主卡/首个变体。
        if log_impressions and Config.SOURCE_STATS_ENABLED and page_cards:
            try:
                source_counts = {}
                for card in page_cards:
                    src = str(card.get("source") or "").strip()
                    if not src:
                        for variant in (card.get("variants") or []):
                            src = str((variant or {}).get("source") or "").strip()
                            if src:
                                break
                    source_counts[src] = source_counts.get(src, 0) + 1
                index_db.record_impressions(source_counts, keyword)
            except Exception as e:
                logger.error(f"记录来源曝光失败: {e}")

        return {
            "keyword": keyword,
            "effective_keyword": (correction or {}).get("to", keyword),
            "correction": correction,
            "total": total_cards,
            "raw_count": raw_count,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "cached": from_cache,
            "sort": sort,
            "elapsed_ms": int((time.perf_counter() - t0) * 1000),
            "disk_counts": full_counts,
            "disk_type_count": disk_type_count,
            "filtered": bool(selected),
            "archive_count": hist_n,
            "realtime_count": real_n,
            "archive_count_raw": archive_raw,
            "realtime_count_raw": realtime_raw,
            "results": page_cards,
            "hidden_dead": hidden_dead_cards[:50],
            "hidden_dead_count": len(hidden_dead_cards),
            "hide_dead": Config.HIDE_DEAD_LINKS,
            "pansou_error": pansou_error,
            "timestamp": datetime.now().isoformat(),
        }

    def _run_search_pipeline(kw, data, only_archive=False, force_refresh=False,
                             progress=None, want_correction=True, deadline=None):
        """
        完整搜索管线（缓存读取 → 本地索引 → 实时波次 → 融合聚合 → 落缓存）。

        progress(stage, frame)：流式阶段回调（经典路径传 None，不启用轮询）——
          'archive'：本地索引先行帧（毫秒级；无历史结果不回调）
          'wave'   ：实时波次中间帧（每有新链接被吸收才推，不推重复空帧）
        缓存命中时不回调，直接返回。返回 (payload, from_cache, correction)。
        """
        # v2：缓存“未筛选的完整结果池”。网盘筛选、排序、分页都在
        # 请求层基于完整池完成，保证 disk_counts 口径一致。
        cache_key = "|".join([
            "v2",
            kw.lower(),
            "a" if only_archive else "r",
            str(data.get("src", "all")),
        ])
        payload = None
        if not force_refresh:
            with _search_cache_lock:
                ent = _search_cache.get(cache_key)
                if ent and time.time() - ent[0] < Config.CACHE_TTL_SECONDS:
                    payload = ent[1]

        def _should_try_correction(payload):
            """
            零结果，或“长中文片名只剩零星弱结果”时尝试纠错。

            错别字查询常常不会真的 0 条：它可能靠前 4 个字命中同系列
            （搜“喜洋洋与灰太狼之破界山海诀”会漏进“喜洋洋与灰太狼之
            虎虎生威”）。这种 1-3 条噪声和 0 条一样，值得再找正确写法。
            """
            n = len(payload.get("cards") or [])
            if n == 0:
                return True
            # 长中文片名只搜到零星几张卡（实测错别字会命中 1-2 张
            # “同系列/同前缀”噪声）：和 0 条一样值得再找正确写法。
            return n <= 3 and len(kw.strip()) >= 8 and _has_cjk(kw)

        def _try_corrections(min_cards=0):
            """
            零/弱结果纠错：ASCII 拼写 + 中文片名近似写法。
            每个候选都真的重搜一遍，只有搜出**更多**资源才采纳——
            保证“您是不是要找”背后确实更好，而不是把唯一结果换掉。
            """
            candidates = []
            try:
                sug = _suggest_correction(index_db, kw)
                if sug:
                    candidates.append(sug)
            except Exception as e:
                logger.error(f"英文拼写纠错失败: {e}")
            if _has_cjk(kw) and len(kw.strip()) >= 4:
                try:
                    candidates.extend(_suggest_title_corrections(
                        kw, pansou_client, index_db=index_db,
                        douban_client=douban_client, deadline=deadline))
                except Exception as e:
                    logger.error(f"中文片名纠错失败: {e}")

            seen = set()
            uniq = []
            for c in candidates:
                c = (c or "").strip()
                key = c.lower()
                if not c or key == kw.lower() or key in seen:
                    continue
                seen.add(key)
                uniq.append(c)

            # 最多复核 2 个候选：每次都是一次完整搜索，多了会拖垮零结果页面
            for cand in uniq[:2]:
                if deadline is not None and time.time() > deadline - 8:
                    break
                try:
                    p2, fc2, _ = _run_search_pipeline(
                        cand, data, only_archive=only_archive,
                        force_refresh=force_refresh,
                        want_correction=False, deadline=deadline)
                except Exception as e:
                    logger.error(f"纠错重搜失败 {cand!r}: {e}")
                    continue
                if len(p2["cards"]) > min_cards:
                    logger.info(f"[纠错] '{kw}' → '{cand}' "
                                f"({min_cards} → {len(p2['cards'])} 张资源卡)")
                    return p2, fc2, {"from": kw, "to": cand}
            return None

        if payload is not None:
            # 零/弱结果缓存也要走纠错：用户重复搜索同一个错别字时，
            # 仍应看到正确片名的结果和“您是不是要找”提示。
            if (want_correction and not only_archive
                    and _should_try_correction(payload)
                    and (deadline is None or time.time() < deadline - 5)):
                fixed = _try_corrections(len(payload.get("cards") or []))
                if fixed:
                    p2, fc2, corr = fixed
                    # 浅拷贝：候选词的缓存池也会被其他搜索复用，
                    # 不能把“从错词纠正过来”的标记写进它的缓存里。
                    p2 = dict(p2)
                    p2["_correction"] = corr
                    _cache_put(cache_key, p2)
                    return p2, fc2, corr
            return payload, True, payload.get("_correction")
        from_cache = False   # 未命中缓存的新搜索：纠错重搜可能再改写它

        # ★ 同关键词并发合并：同 cache_key 已有管线在跑时，等它的完整结果池
        #   （流式请求期间 SSE 每 5s 有 keep-alive 注释帧，连接不会被代理掐断）。
        #   等待超时或主跑者异常退出时才自己补跑，避免请求互相拖死。
        _wait_budget = _SEARCH_INFLIGHT_MAX_WAIT
        if deadline is not None:
            _wait_budget = max(1.0, min(_wait_budget, deadline - time.time()))
        _wait_until = time.time() + _wait_budget
        # force_refresh 语义是"跳过旧结果"，因此不并入在跑的旧管线
        while not force_refresh:
            with _search_inflight_lock:
                ent = _search_inflight.get(cache_key)
                if ent is not None and time.time() - ent.get("started", 0) > _SEARCH_INFLIGHT_STALE:
                    _search_inflight.pop(cache_key, None)
                    ent = None
                if ent is None:
                    _search_inflight[cache_key] = {
                        "event": threading.Event(),
                        "result": None,
                        "started": time.time(),
                    }
                    break
            remain = _wait_until - time.time()
            if remain <= 0:
                # 等太久：主跑管线可能卡住，自己跑一份兜底（不再登记）
                break
            ent["event"].wait(min(remain, 5.0))
            if ent.get("result") is not None:
                return ent["result"]

        def _left():
            """距流式硬性截止还剩多少秒；无 deadline 表示不限"""
            return None if deadline is None else max(0.0, deadline - time.time())

        # ====== 第 1 路：从本地索引库搜索（历史结果）======
        archive_results = []
        try:
            # 不在 SQL 层按网盘过滤：完整池才能算出正确的 disk_counts。
            # 多取候选再过相关度过滤：SQL 是 2-3 字滑窗 OR，50 条容易被噪声占满。
            archive_results = index_db.search(kw, disk_types=None, limit=80)
        except Exception as e:
            logger.error(f"索引搜索失败: {e}")

        # ★ 本地索引也过同一套相关度过滤：
        #   索引检索是 2-3 字滑窗 OR，天然会捞回“还活着/古宅老友记”这类
        #   只共享子串的噪声；不过滤会把实时结果的相关度努力全部抵消。
        try:
            _archive_tokens = _tokenize_query(kw)
            _before = len(archive_results)
            archive_results = [
                it for it in archive_results
                if _matches_query(it, kw, _archive_tokens)
            ]
            if _before and len(archive_results) < _before:
                logger.info(f"[Archive] '{kw}' 相关度过滤 "
                            f"{_before} → {len(archive_results)}")
        except Exception as e:
            logger.error(f"索引相关度过滤失败: {e}")

        for item in archive_results:
            item["from_archive"] = True

        # 点击热度（中间帧与最终融合共用一份）
        try:
            click_map = index_db.get_click_stats(kw)
        except Exception as e:
            logger.error(f"读取点击统计失败: {e}")
            click_map = {}

        # 来源质量降权表（样本足够 + 高死链 + 低独家才生效；缓存 5 分钟）
        source_penalties = {}
        if Config.SOURCE_QUALITY_RANKING:
            try:
                source_penalties = analyzer.get_source_penalty_map()
            except Exception as e:
                logger.error(f"读取来源降权表失败: {e}")

        # ---- 波次状态（经典路径 progress=None，推帧逻辑自动跳过）----
        merged_realtime = {}
        consumed = [0]        # 增量吸收指针（指向 collected 快照下标）
        _frame_links = [-1]   # 上一帧已推送的实时链接数（防重复空帧）
        pansou_error = None
        query_tokens = None

        def _absorb_items(items, tokens):
            """合并一批结果进 merged_realtime（相关度过滤 + 智能去重；幂等可增量）"""
            nonlocal pansou_error
            for item in items:
                if item[0] != "ok":
                    if not pansou_error:
                        pansou_error = item[1]
                    continue
                _, _variant_key, links = item
                for ln in links:
                    u = ln.get("url", "")
                    if not u:
                        continue
                    # 网盘类型过滤统一放到融合后的完整池上做，
                    # 否则筛选请求的 disk_counts 只会统计已筛选集合。
                    # ★ 相关性过滤：剔除“子词/修饰词顺带命中”但与原查询无关的结果，
                    #   这是把“一个都没有”提升到“一排都是可用结果”的关键。
                    if not _matches_query(ln, kw, tokens):
                        continue
                    ln["from_archive"] = False
                    old = merged_realtime.get(u)
                    if old is None or _link_quality(ln) > _link_quality(old):
                        merged_realtime[u] = ln

        def _emit_wave_frame():
            """实时结果有增长才推中间帧"""
            if progress is None:
                return
            if len(merged_realtime) > _frame_links[0]:
                _frame_links[0] = len(merged_realtime)
                frame = _fusion_payload(
                    kw, archive_results, list(merged_realtime.values()),
                    click_map, pansou_error, source_penalties)
                if frame is not None:
                    progress("wave", frame)

        # 本地索引先行帧（毫秒级）：先把历史结果发出去
        if progress is not None and archive_results:
            frame = _fusion_payload(kw, archive_results, [], click_map,
                                    source_penalties=source_penalties)
            if frame is not None:
                progress("archive", frame)

        # ====== 第 2 路：实时搜索（PanSou 多变体 + 学霸盘书源）======
        # 主变体+补充变体+书源一起并发扇出；补充结果必须含原词才能入选，
        # 只扩召回、不污染主查询的相关性。
        realtime_results = []
        if not only_archive:
            try:
                variants = _build_search_variants(kw)
                supplements = _build_supplement_variants(kw)
                # 严格相关度过滤的信息词（仅 ASCII；长尾兜底时换扩展表）
                query_tokens = _tokenize_query(kw)

                def _run_wave(query_list, with_xuebapan=False, on_new=None):
                    """
                    并发执行一批查询（可附带学霸盘书源），返回 collected。
                    on_new 提供时按 STREAM_POLL_SECONDS 轮询线程完成度，
                    每有新结果就回调一次快照（流式渐进出帧）；
                    否则一次性等全部结束（经典路径，行为与旧实现一致）。
                    """
                    collected = []
                    lock = threading.Lock()

                    def _one_pansou(variant):
                        try:
                            resp = pansou_client.search(
                                variant,
                                src=data.get("src", "all"),
                                plugins=data.get("plugins"),
                                # 网盘过滤留在融合层，保证计数基于完整池
                                cloud_types=None,
                                force_refresh=force_refresh,
                                result_type="all",
                            )
                            with lock:
                                if resp.get("error"):
                                    collected.append(("err", resp.get("message")))
                                else:
                                    collected.append(
                                        ("ok", variant, searcher.extract_links(resp)))
                        except Exception as e:
                            with lock:
                                collected.append(("err", str(e)))

                    def _one_xuebapan():
                        try:
                            links = _xuebapan.search(kw) if _xuebapan.enabled else []
                            with lock:
                                if links:
                                    collected.append(("ok", "xuebapan", links))
                        except Exception as e:
                            with lock:
                                collected.append(("err", f"xuebapan: {e}"))

                    threads = [threading.Thread(target=_one_pansou, args=(v,),
                                                daemon=True)
                               for v in query_list]
                    if with_xuebapan:
                        threads.append(threading.Thread(target=_one_xuebapan,
                                                        daemon=True))
                    for t in threads:
                        t.start()
                    wave_timeout = max(Config.PANSOU_TIMEOUT + 5,
                                       Config.XUEBAPAN_TIMEOUT + 5)
                    if on_new is None:
                        for t in threads:
                            t.join(timeout=wave_timeout)
                        return collected
                    # 流式：小步轮询，每有新结果回调一次快照（线程安全）
                    left = _left()
                    wave_deadline = time.time() + (
                        wave_timeout if left is None else min(wave_timeout, left))
                    seen = 0
                    while time.time() < wave_deadline:
                        time.sleep(Config.STREAM_POLL_SECONDS)
                        with lock:
                            snapshot = list(collected)
                        if len(snapshot) > seen:
                            seen = len(snapshot)
                            on_new(snapshot)
                        if not any(t.is_alive() for t in threads):
                            break
                    for t in threads:
                        t.join(timeout=1.0)
                    with lock:
                        snapshot = list(collected)
                    if len(snapshot) > seen:
                        on_new(snapshot)
                    return collected

                def _absorb_snapshot(snapshot):
                    """按指针吸收增量并尝试推帧（经典路径只吸收不推帧）"""
                    items = snapshot[consumed[0]:]
                    if items:
                        consumed[0] = len(snapshot)
                        _absorb_items(items, query_tokens)
                    _emit_wave_frame()

                combined = _run_wave(
                    variants + supplements, with_xuebapan=True,
                    on_new=(_absorb_snapshot if progress is not None else None))
                # 收尾吸收（经典路径即全量吸收；流式补最后到达的漏网结果）
                _absorb_snapshot(combined)

                # ★ 低召回重查：吃满 PanSou 异步爬取带来的服务端缓存增长
                retries = 0
                while (0 < len(merged_realtime) < Config.RECALL_REQUERY_THRESHOLD
                       and retries < Config.RECALL_REQUERY_MAX):
                    left = _left()
                    if left is not None and left < Config.RECALL_REQUERY_WAIT + 1:
                        break
                    time.sleep(Config.RECALL_REQUERY_WAIT)
                    consumed[0] = 0   # 每波 collected 是新列表，指针必须归零
                    wave = _run_wave(
                        variants,
                        on_new=(_absorb_snapshot if progress is not None else None))
                    _absorb_snapshot(wave)
                    retries += 1

                # ★ 长尾兜底：严格过滤颗粒无收时，用扩展信息词（含 CJK 段）
                #   重滤同一批结果——长书名/教材名常被“全名必须出现”误杀。
                #   收紧：扩展词 >=2 个时要求至少命中 2 个（单词命中太容易
                #   被无关结果顺带满足，会把 0 结果变成满屏噪声）。
                if not merged_realtime and len(kw) > 6:
                    ext_tokens = _tokenize_query(kw, extended=True)
                    min_hits = 2 if len(ext_tokens) >= 2 else 1
                    relaxed = [ln for ln in _iter_collected_links(combined)
                               if _token_hits(ln, kw, ext_tokens) >= min_hits]
                    for ln in relaxed:
                        u = ln.get("url", "")
                        if not u:
                            continue
                        ln["from_archive"] = False
                        old = merged_realtime.get(u)
                        if old is None or _link_quality(ln) > _link_quality(old):
                            merged_realtime[u] = ln
                    if merged_realtime:
                        logger.info(f"[长尾] '{kw[:40]}' 宽松兜底命中 {len(merged_realtime)} 条")
                        _emit_wave_frame()

                realtime_results = list(merged_realtime.values())

                # ★★★ 将实时搜索结果写入索引库 ★★★
                # “渐进式索引”的核心 —— 每次搜索的结果都存入索引
                try:
                    stored = index_db.store_links(realtime_results)
                    if stored > 0:
                        logger.info(f"[Search] '{kw}' → 索引新增 {stored} 条")
                except Exception as e:
                    logger.error(f"写入索引失败: {e}")

                # ★ 用索引库的智能检测状态给实时结果补上有效性：
                #   早被巡检/举报确认失效的链接，不能因为“实时”就当存活展示
                try:
                    _vmap = index_db.get_validity_map(
                        [ln.get("url") for ln in realtime_results])
                    for ln in realtime_results:
                        vi = _vmap.get(ln.get("url"))
                        if vi:
                            ln["validity"] = vi["validity"]
                            ln["state_summary"] = vi["state_summary"]
                            ln["checked_at"] = vi["checked_at"]
                except Exception as e:
                    logger.error(f"补全有效性状态失败: {e}")

            except Exception as e:
                pansou_error = str(e)
                logger.error(f"PanSou 搜索失败: {e}")

        # ====== 融合结果 ======
        for item in realtime_results:
            item["from_archive"] = False

        # ★★★ 跨源去重 + 综合排序 + 同资源聚合 + 点击热度置顶 ★★★
        ranked_pool, hist_n, real_n = _dedup_and_rank(
            archive_results, realtime_results, kw, click_map=click_map,
            source_penalties=source_penalties
        )
        cards = _group_resources(
            ranked_pool, limit=None, source_penalties=source_penalties,
            keyword=kw)

        # ★ 智能隐藏：全部链接确认失效的资源卡移出主列表
        #   （hidden_dead 随响应返回，前端可展开“已隐藏的失效资源”）
        cards, hidden_dead_cards = _hide_dead_cards(cards)
        if hidden_dead_cards:
            logger.info(f"[智能检测] '{kw}' 隐藏 {len(hidden_dead_cards)} "
                        f"张失效资源卡")

        # ★ 投递封面预热：前排卡片标题后台解析，下次请求秒出图
        try:
            _enqueue_warm(_poster_query(c.get("title") or "")
                          for c in cards[:12])
        except Exception:
            pass

        # ★ 记录搜索历史（仅真实搜索计数，缓存翻页不计）
        try:
            index_db.record_search(kw)
        except Exception as e:
            logger.error(f"记录搜索历史失败: {e}")

        # ★ 网盘计数：基于隐藏失效后的完整结果池，按主卡 disk_type 统计。
        #   这样“全部”状态下各类型计数之和 = 结果总数；筛选只影响展示，
        #   不会让计数变成筛选后的子集。
        disk_counts = {}
        for c in cards:
            dt = str(c.get("disk_type") or "").strip().lower() or "others"
            disk_counts[dt] = disk_counts.get(dt, 0) + 1

        payload = {
            "cards": cards,
            "disk_counts": disk_counts,
            "raw_count": len(ranked_pool),
            "hist": hist_n,
            "real": real_n,
            "archive_raw": len(archive_results),
            "realtime_raw": len(realtime_results),
            "hidden_dead": hidden_dead_cards[:50],
            "hidden_dead_count": len(hidden_dead_cards),
            "error": pansou_error,
        }
        _cache_put(cache_key, payload)

        # ★ 搜索后静默补检：前排卡片里未检测/过期的链接后台送检，
        #   有新结论会失效本关键词缓存（下次搜索即见降权/隐藏生效）
        if Config.SMART_CHECK_ENABLED:
            try:
                _background_smart_check(checker, cards, kw)
            except Exception as e:
                logger.error(f"后台补检调度失败: {e}")

        # ★ 纠错：零/弱结果时自动重搜（Chrome 式“您是不是要找”）
        #   英文按拼写相似度，中文按片名字符相似度（同音字/漏字/多字）。
        #   注：不看 pansou_error——单个变体失败不应阻止纠错
        correction = None
        if (want_correction and not only_archive
                and _should_try_correction(payload)
                and (deadline is None or time.time() < deadline - 5)):
            fixed = _try_corrections(len(payload.get("cards") or []))
            if fixed:
                payload, from_cache, correction = fixed
                # 同上：p2 可能正是候选词的结果池缓存，先浅拷贝再打标记
                payload = dict(payload)
                payload["_correction"] = correction
                # 用纠正后的结果覆盖原词的零结果缓存：
                # 下次再搜错词，直接命中正确结果，不再白跑一轮 PanSou。
                _cache_put(cache_key, payload)

        # ★ 零结果守望队列：真没有的词入队，harvester 每轮全源重试
        if not payload["cards"]:
            try:
                index_db.record_zero_result(kw)
                logger.info(f"[守望] '{kw}' 零结果，已加入守望队列")
            except Exception as e:
                logger.error(f"记录守望队列失败: {e}")

        return payload, from_cache, correction

    @api_bp.route("/search", methods=["POST"])
    def search():
        """
        融合搜索（分页 + 结果缓存）—— 项目的核心 API

        1. 先在本地索引库中搜（毫秒级返回历史结果）
        2. 同时调 PanSou 搜实时结果（秒级）
        3. 融合 + 同资源聚合后按页返回
        4. 完整结果池按关键词缓存（TTL 内翻页不重复请求 PanSou）

        请求体:
        {
            "kw": "庆余年",              // 必填
            "page": 1,                   // 可选，页码（1 起）
            "page_size": 20,             // 可选，每页资源卡数（5~60）
            "disk_types": ["baidu"],     // 可选，网盘类型过滤
            "sort": "relevance",         // 可选，relevance/time/name
            "only_archive": false,       // 可选，只搜历史索引（不调 PanSou）
            "src": "all",               // 可选，PanSou 的 src 参数
            "force_refresh": false,      // 可选，强制刷新（跳过缓存）
            "skip_stats": false          // 可选，评测/机器人请求不记来源曝光
        }
        """
        t0 = time.perf_counter()
        data = request.get_json(force=True, silent=True) or {}
        keyword = data.get("kw", "").strip()
        if not keyword:
            return jsonify({"error": "关键词不能为空"}), 400

        disk_types = _parse_disk_types(data.get("disk_types"))
        sort = str(data.get("sort") or "relevance").strip().lower()
        if sort not in ("relevance", "time", "name"):
            sort = "relevance"
        only_archive = bool(data.get("only_archive", False))
        force_refresh = bool(data.get("force_refresh", False))
        skip_stats = bool(data.get("skip_stats", False))
        try:
            page = max(1, int(data.get("page", 1) or 1))
        except (TypeError, ValueError):
            page = 1
        try:
            page_size = min(60, max(5, int(data.get("page_size", 20) or 20)))
        except (TypeError, ValueError):
            page_size = 20

        payload, from_cache, correction = _run_search_pipeline(
            keyword, data, only_archive=only_archive,
            force_refresh=force_refresh)

        return jsonify(_slice_response(
            payload, keyword, disk_types, sort, page, page_size,
            correction, from_cache, t0, log_impressions=not skip_stats))

    @api_bp.route("/search/stream", methods=["POST"])
    def search_stream():
        """
        流式搜索（SSE）—— 渐进出结果，不等全量检索完成：

          * 本地索引毫秒级先发一帧（历史结果）；
          * 实时波次每吸收到新链接就再发一帧（约每 0.8s 轮询一次）；
          * 最后一帧 stage="done" 携带完整结果（与 /api/search 完全一致）。

        每帧都是完整的 /api/search 响应结构（附加 stage/seq/done 字段），
        前端可无差别渐进渲染；与经典接口共用同一份结果缓存——
        流式完成后翻页/筛选走 /api/search 直接命中缓存。

        请求体与 /api/search 一致；由 STREAM_SEARCH 开关控制，
        关闭时返回 404，前端自动回退经典接口。
        """
        if not Config.STREAM_SEARCH:
            return jsonify({"error": "流式搜索未开启"}), 404
        t0 = time.perf_counter()
        data = request.get_json(force=True, silent=True) or {}
        keyword = data.get("kw", "").strip()
        if not keyword:
            return jsonify({"error": "关键词不能为空"}), 400

        disk_types = _parse_disk_types(data.get("disk_types"))
        sort = str(data.get("sort") or "relevance").strip().lower()
        if sort not in ("relevance", "time", "name"):
            sort = "relevance"
        only_archive = bool(data.get("only_archive", False))
        force_refresh = bool(data.get("force_refresh", False))
        skip_stats = bool(data.get("skip_stats", False))
        try:
            page = max(1, int(data.get("page", 1) or 1))
        except (TypeError, ValueError):
            page = 1
        try:
            page_size = min(60, max(5, int(data.get("page_size", 20) or 20)))
        except (TypeError, ValueError):
            page_size = 20

        deadline = time.time() + Config.STREAM_MAX_SECONDS

        @stream_with_context
        def gen():
            # 管线跑在工作线程，帧经队列送到 HTTP 层：管线里的阻塞等待
            # 不会拖慢已产生帧的下发；5s 无帧发 SSE 注释行作代理保活。
            q = queue.Queue()

            def _progress(stage, frame):
                q.put(("frame", stage, frame))

            def _work():
                try:
                    payload, from_cache, correction = _run_search_pipeline(
                        keyword, data, only_archive=only_archive,
                        force_refresh=force_refresh, progress=_progress,
                        want_correction=True, deadline=deadline)
                    q.put(("final", payload, from_cache, correction))
                except Exception as e:
                    logger.error(f"流式搜索失败: {e}")
                    q.put(("error", str(e)))

            threading.Thread(target=_work, daemon=True,
                             name="search-stream").start()

            seq = 0
            while True:
                try:
                    msg = q.get(timeout=5)
                except queue.Empty:
                    yield ": keep-alive\n\n"
                    continue
                kind = msg[0]
                if kind == "frame":
                    _, _stage, frame = msg
                    seq += 1
                    resp = _slice_response(
                        frame, keyword, disk_types, sort, page, page_size,
                        None, False, t0)
                    resp.update({"stage": "partial", "seq": seq, "done": False})
                    yield f"data: {json.dumps(resp, ensure_ascii=False)}\n\n"
                elif kind == "final":
                    _, payload, from_cache, correction = msg
                    seq += 1
                    resp = _slice_response(
                        payload, keyword, disk_types, sort, page, page_size,
                        correction, from_cache, t0, log_impressions=not skip_stats)
                    resp.update({"stage": "done", "seq": seq, "done": True})
                    yield f"data: {json.dumps(resp, ensure_ascii=False)}\n\n"
                    return
                else:
                    _, err = msg
                    seq += 1
                    yield ("data: " + json.dumps(
                        {"stage": "error", "seq": seq, "done": True,
                         "error": err}, ensure_ascii=False) + "\n\n")
                    return

        resp = Response(gen(), mimetype="text/event-stream")
        resp.headers["Cache-Control"] = "no-cache"
        # nginx 反代不缓冲 SSE，帧才能实时到达客户端
        resp.headers["X-Accel-Buffering"] = "no"
        return resp

    @api_bp.route("/history", methods=["GET"])
    def get_history():
        """搜索历史列表（默认最近 20 条）——**仅站长**。

        历史表是全站一份的聚合数据（keyword 为主键），不是"某个访客的历史"；
        前端已经把"我的历史"改成浏览器本地存储，这里只留给站长做播种/查看。
        """
        if not is_station(request):
            return jsonify({"error": "无权访问该功能"}), 403
        try:
            limit = min(100, max(1, int(request.args.get("limit", "20"))))
        except (TypeError, ValueError):
            limit = 20
        return jsonify({"history": index_db.get_history(limit)})

    @api_bp.route("/history", methods=["DELETE"])
    def delete_history():
        """删除搜索历史：?kw=xxx 删单条，不传 kw 清空全部——**仅站长**。

        这条以前是**完全无鉴权**的：任何人一条请求就能清空全站搜索历史。
        """
        if not is_station(request):
            return jsonify({"error": "无权访问该功能"}), 403
        kw = request.args.get("kw", "").strip()
        try:
            deleted = index_db.delete_history(kw or None)
            return jsonify({"deleted": deleted})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @api_bp.route("/click", methods=["GET"])
    def click():
        """
        结果点击中转：记录点击 → 后台即时体检 → 失效缓存 → 302 到真实网盘。

        参数:
            u:  目标链接（必须 http/https；磁力/ed2k 不支持中转，前端走直链）
            kw: 触发搜索的关键词（用于"本词下点过的置顶"加权）
            dt: 网盘类型（可选，供即时体检选对检测器）
            pw: 提取码（可选，同上）
            src: 结果来源（可选；用于来源质量统计，缺省回退 link_index 反查）
        """
        target = request.args.get("u", "").strip()
        kw = request.args.get("kw", "").strip()
        src = request.args.get("src", "").strip()
        if not target or not re.match(r"^https?://", target, re.IGNORECASE):
            return jsonify({"error": "仅支持 http(s) 链接中转"}), 400

        try:
            index_db.record_click(target, kw, source=src)
        except Exception as e:
            logger.error(f"记录点击失败: {e}")

        # ★ 点击后智能体检：走有效性状态机（首次失败→疑似降权，
        #   连续确认→失效），确认失效撤销热度并刷新该词的结果缓存。
        _async_check_link(
            checker, index_db, target,
            request.args.get("dt", "").strip(),
            request.args.get("pw", "").strip(),
            kw=kw,
        )

        # 失效该关键词的缓存，让"点过的置顶"下次搜索立刻生效
        if kw:
            needle = "|" + kw.lower() + "|"
            with _search_cache_lock:
                for k in [k for k in _search_cache if needle in k]:
                    _search_cache.pop(k, None)

        return redirect(target, code=302)

    @api_bp.route("/report/dead", methods=["POST"])
    def report_dead():
        """
        失效举报：用户点开发现"分享已失效"时一键上报。
        立即打失效标记 + 撤销该链接全部点击热度 + 失效关键词缓存。
        （个人部署工具，信任本地用户；举报记录进日志）
        """
        data = request.get_json(force=True, silent=True) or {}
        url = (data.get("u") or "").strip()
        kw = (data.get("kw") or "").strip()
        if not url or not re.match(r"^https?://", url, re.IGNORECASE):
            return jsonify({"error": "链接无效"}), 400

        try:
            index_db.mark_reported_dead(url)
            heat = index_db.clear_click_heat(url)
        except Exception as e:
            return jsonify({"error": f"标记失败: {e}"}), 500

        _invalidate_kw_cache(kw)

        logger.info(f"[举报] 失效: {url[:60]} kw={kw!r} (撤销热度 {heat} 条)")
        return jsonify({"ok": True, "heat_cleared": heat})

    @api_bp.route("/archive", methods=["GET"])
    def browse_archive():
        """
        浏览/搜索历史索引

        参数:
            kw: 搜索关键词（可选，不传则返回最新）
            disk_type: 网盘类型过滤
            only_alive: 是否只存活（默认 true）
            page_size: 每页条数
        """
        keyword = request.args.get("kw", "").strip()
        disk_type_str = request.args.get("disk_type", "")
        only_alive = request.args.get("only_alive", "true").lower() == "true"
        limit = int(request.args.get("page_size", "50"))

        disk_types = None
        if disk_type_str:
            disk_types = [t.strip() for t in disk_type_str.split(",")]

        try:
            if keyword:
                # SQL 是 2-3 字滑窗 OR 召回，直接 limit 会让噪声占满名额。
                # 多取候选再过与 /search 相同的相关度过滤，最后截回请求条数。
                results = index_db.search(
                    keyword, disk_types=disk_types,
                    limit=max(limit * 2, 100), only_alive=only_alive
                )
                tokens = _tokenize_query(keyword)
                results = [
                    it for it in results
                    if _matches_query(it, keyword, tokens)
                ][:limit]
            else:
                # 不传关键词则返回最新收录的
                conn = index_db._conn
                sql = "SELECT * FROM link_index"
                params = []
                where = []
                if disk_types:
                    placeholders = ",".join(["?" for _ in disk_types])
                    where.append(f"disk_type IN ({placeholders})")
                    params.extend(disk_types)
                if only_alive:
                    where.append("alive = 1")
                if where:
                    sql += " WHERE " + " AND ".join(where)
                sql += " ORDER BY last_seen DESC LIMIT ?"
                params.append(limit)
                rows = conn.execute(sql, params).fetchall()
                results = [dict(r) for r in rows]

            return jsonify({
                "keyword": keyword or None,
                "total": len(results),
                "results": results,
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @api_bp.route("/stats", methods=["GET"])
    def get_stats():
        """获取系统统计数据"""
        try:
            index_stats = index_db.get_stats()
            analyzer_stats = {
                "disk_distribution": analyzer.get_disk_type_distribution(),
                "health_overview": analyzer.get_link_health_overview(),
                "source_distribution": analyzer.get_source_distribution(),
                "growth": analyzer.get_index_growth(days=14),
            }
            # ACCEL_NO_BACKGROUND=1 时 harvester 属性存在但为 None，
            # 不能只用 hasattr 判断，否则 /api/stats 会 500。
            harvester = getattr(searcher, "harvester", None)
            harvester_stats = harvester.get_stats() if harvester else {}
            checker_stats = checker.get_stats() if checker else {}
            if checker:
                # 后台控制台需要真实运行状态（此前 StatsPage 只能硬编码）
                checker_stats["running"] = checker.is_running()

            return jsonify({
                "index": index_stats,
                "analyzer": analyzer_stats,
                "harvester": harvester_stats,
                "checker": checker_stats,
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @api_bp.route("/stats/sources", methods=["GET"])
    def get_source_stats():
        """
        来源质量表（按天窗口 + 查询类型）。

        参数:
            days: 统计窗口天数，默认 30；0 表示全部时间（最大 365）
            category: all/movie/study/software/music，默认 all

        口径：死链率 = dead / 已检测；未检测单独展示，不参与分母。
        曝光/点击从本版本升级后开始按天累计。
        """
        try:
            days = int(request.args.get("days", "30"))
        except (TypeError, ValueError):
            days = 30
        days = max(0, min(365, days))
        category = (request.args.get("category") or "all").strip().lower()
        if category not in ("all", "movie", "study", "software", "music"):
            category = "all"
        force = (request.args.get("refresh") or "").strip().lower() in (
            "1", "true", "yes")
        try:
            return jsonify(analyzer.get_source_quality(
                days=days, category=category, force=force))
        except Exception as e:
            logger.error(f"来源质量统计失败: {e}")
            return jsonify({"error": str(e)}), 500

    @api_bp.route("/health", methods=["GET"])
    def health():
        """健康检查"""
        pansou_status = pansou_client.check_health()
        return jsonify({
            "status": "ok",
            "pansou": pansou_status.get("status", "unknown"),
            "index_db_size": index_db.get_stats().get("total", 0),
            "timestamp": datetime.now().isoformat(),
        })

    @api_bp.route("/check/batch", methods=["POST"])
    def check_batch():
        """
        智能批量有效性检测：前端渲染当前页后，把可见链接送来体检，
        就地刷新"有效/疑似失效/已失效"徽章与降权排序。

        请求体: {"items": [{"url", "disk_type", "password"}], "force": false}
        返回:   {"results": {url: {"validity", "state", "summary",
                                   "checked_at", "fresh"}}}
          validity: ok=有效 / suspect=疑似失效 / dead=确认失效 / ''=未检测
          fresh=true 表示复用保鲜期内的既有结果，未真正请求检测源。
        """
        data = request.get_json(force=True, silent=True) or {}
        raw_items = data.get("items") or []
        items = []
        seen = set()
        for it in raw_items[:60]:
            if not isinstance(it, dict):
                continue
            url = str(it.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            items.append({
                "url": url,
                "disk_type": str(it.get("disk_type") or "").strip(),
                "password": str(it.get("password") or "").strip(),
            })
        if not items:
            return jsonify({"results": {}})
        try:
            results = checker.check_items(items, force=bool(data.get("force")))
            return jsonify({"results": results})
        except Exception as e:
            logger.error(f"批量检测失败: {e}")
            return jsonify({"error": str(e)}), 500

    # ==========================================
    # 热搜词 / 豆瓣榜单 / 封面海报
    # ==========================================

    @api_bp.route("/hot", methods=["GET"])
    def hot_keywords():
        """热搜词（索引热词 top20，已降噪过滤）+ 索引总量，供搜索页热搜行"""
        try:
            st = index_db.get_stats()
            raw = st.get("hot_keywords") or []
            hot = [h for h in raw if _hot_ok(h.get("title") or "")][:20]
            return jsonify({"hot": hot, "total": st.get("total", 0)})
        except Exception as e:
            return jsonify({"hot": [], "total": 0, "error": str(e)})

    # ---------- 项目仓库信息（左下角 GitHub 入口的星标数） ----------
    _repo_cache = {"t": 0.0, "data": None}
    _repo_lock = threading.Lock()
    GITHUB_REPO = os.getenv("GITHUB_REPO", "akai-do/supansou")
    REPO_TTL = int(os.getenv("GITHUB_REPO_TTL", "21600"))   # 6 小时：星标变化很慢

    def _parse_stars(msg):
        """把 shields 的 message 解析成整数：'123' / '9.3k' / '1.2M' → int。"""
        s = (msg or "").strip().lower().replace(",", "")
        mult = 1
        if s.endswith("k"):
            mult, s = 1000, s[:-1]
        elif s.endswith("m"):
            mult, s = 1000000, s[:-1]
        try:
            return int(float(s) * mult)
        except ValueError:
            return None

    @api_bp.route("/repo", methods=["GET"])
    def repo_info():
        """返回项目仓库的星标数（给左下角 GitHub 入口显示）。

        数据源优先 **shields.io**：GitHub 未认证 API 的限流是"每 IP 每小时 60 次"
        且被同一出口的所有使用者共享（实测本机出口已打到 `x-ratelimit-remaining = 0`），
        直连经常拿不到；shields 用自己的配额，是这类需求的通行做法。GitHub API 仅兜底。
        服务端缓存 6 小时、所有人共享一份；取不到就返回 stars=null，
        前端照常显示入口、只是不显示星标数字。
        """
        now = time.time()
        with _repo_lock:
            c = _repo_cache["data"]
            if c and now - _repo_cache["t"] < REPO_TTL:
                return jsonify({**c, "cached": True})

        data = None
        # ① shields.io
        try:
            r = requests.get(f"https://img.shields.io/github/stars/{GITHUB_REPO}.json",
                             timeout=8, headers={"User-Agent": "DuPanSou-Archive"})
            if r.status_code == 200:
                txt = str(r.json().get("message") or "").strip()
                if txt:
                    data = {
                        "repo": GITHUB_REPO,
                        "url": f"https://github.com/{GITHUB_REPO}",
                        "stars": _parse_stars(txt),
                        "stars_text": txt,
                        "source": "shields",
                        "fetched_at": int(now),
                    }
        except Exception as e:
            logger.info("[repo] shields 取星标失败：%s", e)

        # ② GitHub API 兜底（可能被限流）
        if not data or data.get("stars") is None:
            try:
                r = requests.get(f"https://api.github.com/repos/{GITHUB_REPO}",
                                 timeout=8, headers={
                                     "User-Agent": "DuPanSou-Archive",
                                     "Accept": "application/vnd.github+json"})
                if r.status_code == 200:
                    j = r.json()
                    n = int(j.get("stargazers_count") or 0)
                    data = {
                        "repo": GITHUB_REPO,
                        "url": j.get("html_url") or f"https://github.com/{GITHUB_REPO}",
                        "stars": n,
                        "stars_text": str(n),
                        "forks": int(j.get("forks_count") or 0),
                        "source": "github-api",
                        "fetched_at": int(now),
                    }
            except Exception as e:
                logger.info("[repo] GitHub API 取星标失败：%s", e)

        with _repo_lock:
            if data:
                _repo_cache["t"], _repo_cache["data"] = now, data
            stale = _repo_cache["data"]
        if data:
            return jsonify({**data, "cached": False})
        if stale:      # 拿不到新的就回上一次的，别让前端闪
            return jsonify({**stale, "cached": True, "stale": True})
        return jsonify({"repo": GITHUB_REPO, "stars": None, "stars_text": None,
                        "url": f"https://github.com/{GITHUB_REPO}"})

    # ---------- 「本机加速器」安装包下发 ----------
    # 加速必须在客户端所在机器上执行（限速计时器在百度客户端进程里，网页无法从外部
    # 注入），所以站点只能引导用户下载这个本机小程序。包放在持久化的 data 目录，
    # 站长把打好的 zip 传上去即可（默认文件名见下，可用 ACCEL_AGENT_ZIP 改）。
    ACCEL_AGENT_ZIP = os.getenv("ACCEL_AGENT_ZIP", os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "data", "DuPanSou加速器.zip"))

    @api_bp.route("/agent", methods=["GET"])
    def download_agent():
        """下载「本机加速器」压缩包（约 13MB，无需装 Python）。"""
        path = os.path.abspath(ACCEL_AGENT_ZIP)
        if not os.path.isfile(path):
            logger.info("[agent] 安装包不存在：%s", path)
            return jsonify({
                "error": "本机加速器安装包尚未上传（站长请把 zip 放到 data/ 目录）",
                "expected_path": path,
            }), 404
        from flask import send_file
        # conditional=True：支持 Range/断点续传（13MB 的包，弱网下能续）
        return send_file(path, as_attachment=True,
                         download_name="DuPanSou加速器.zip",
                         mimetype="application/zip", conditional=True)

    @api_bp.route("/douban/hot", methods=["GET"])
    def douban_hot():
        """
        豆瓣榜单（v2 富信息：card_subtitle/评分人数/大图，结果缓存 1h）

        参数:
            collection: 榜单 ID（movie_hot/movie_latest/movie_gems/tv_hot/
                        tv_domestic/tv_american/tv_korean/tv_japanese/
                        tv_animation/tv_variety_show/tv_documentary）
            start/count: 分页
            （兼容旧参数 type/category/tag）
        """
        collection = request.args.get("collection", "").strip()
        try:
            start = max(0, int(request.args.get("start", "0")))
            count = min(50, max(1, int(request.args.get("count", "25"))))
        except (TypeError, ValueError):
            start, count = 0, 25

        if collection and douban_client:
            items = douban_client.collection_items(collection, start, count)
            return jsonify({
                "items": items,
                "enabled": bool(douban_client.enabled),
                "error": douban_client.last_error,
            })

        # 旧参数兼容路径（type/category/tag）
        type_ = request.args.get("type", "movie")
        tag = request.args.get("tag", "热门")
        items = []
        if douban_client:
            items = douban_client.hot(type_, tag,
                                      page_limit=count, page_start=start)
        return jsonify({
            "items": items,
            "enabled": bool(douban_client and douban_client.enabled),
            "error": douban_client.last_error if douban_client else "未启用",
        })

    @api_bp.route("/poster/batch", methods=["POST"])
    def poster_batch():
        """
        资源标题批量匹配豆瓣海报封面

        请求: {"titles": ["...", ...]}（≤12 条）
        返回: {"posters": {"标题": "doubanio 图 URL 或 null"}}
        匹配结果永久缓存（含"无匹配"），命中缓存零豆瓣请求。
        """
        if not Config.ENABLE_POSTERS or not douban_client or not douban_client.enabled:
            return jsonify({"posters": {}})
        data = request.get_json(force=True, silent=True) or {}
        titles = []
        seen = set()
        for t in (data.get("titles") or [])[:12]:
            t = str(t or "").strip()
            if t and t not in seen:
                seen.add(t)
                titles.append(t)
        if not titles:
            return jsonify({"posters": {}})

        out = {}
        keys = {t: _poster_query(t) for t in titles}
        cached = index_db.get_posters([k for k in keys.values() if k])
        missing = []
        for t in titles:
            k = keys[t]
            if not k:
                out[t] = None
            elif k in cached:
                out[t] = cached[k] or None
            else:
                missing.append(t)
        if missing:
            # ★ 单飞解析：相同查询词全局只跑一次；内联等待 2.5s，
            #   没等到的下次搜索命中缓存（预热 worker 通常已提前解析）
            key_by_title = {t: keys[t] for t in missing}
            futs = _submit_keys(list(key_by_title.values()))
            res = _wait_futures(futs, wait=2.5)
            for t in missing:
                out[t] = res.get(key_by_title[t])
        return jsonify({"posters": out})

    @api_bp.route("/poster/img", methods=["GET"])
    def poster_img():
        """图片代理：/api/poster/img?u=<图片 URL>

        两条路互为兜底，顺序按域名选：
          * **豆瓣图床**先走 DoubanClient.proxy_image —— 它靠轮换 img1-9 子域绕过
            豆瓣的 JS 挑战（实测直接抓 img1 会返回 418）；
          * **其它域名**（Telegram CDN 等）先走 imgcache —— 服务端回源 + 磁盘缓存，
            部署在香港的服务器能抓到国内直连不通的 TG 图，抓一次之后所有人秒出。
        安全：imgcache 做 SSRF 校验（每一跳都验、只允许公网地址）+ 体积/跳转上限 + 按 IP 限流。
        """
        from urllib.parse import urlparse
        u = request.args.get("u", "").strip()

        ip = request.remote_addr or "?"
        if not imgcache.LIMITER.allow(ip):
            return jsonify({"error": "图片请求过于频繁，请稍后再试"}), 429

        host = (urlparse(u).netloc or "").lower()
        douban_like = host == "doubanio.com" or host.endswith(".doubanio.com")
        order = ("douban", "cache") if douban_like else ("cache", "douban")

        def _stream(resp_obj):
            def gen():
                try:
                    for chunk in resp_obj.iter_content(8192):
                        if chunk:
                            yield chunk
                finally:
                    resp_obj.close()
            r = Response(gen(),
                         content_type=resp_obj.headers.get("Content-Type", "image/jpeg"))
            r.headers["Cache-Control"] = "public, max-age=604800"
            return r

        errors = []
        for src in order:
            try:
                if src == "douban":
                    return _stream(douban_client.proxy_image(u))
                body, ct = imgcache.fetch(u)
                resp = Response(body, content_type=ct)
                resp.headers["Cache-Control"] = "public, max-age=604800"
                return resp
            except imgcache.UnsafeURL as e:
                # URL 本身不合法：换另一条路也没用，直接拒绝
                return jsonify({"error": f"该图片地址不被允许：{e}"}), 400
            except Exception as e:
                errors.append(f"{src}: {e}")
        return jsonify({"error": "拉取失败：" + "；".join(errors)}), 502

    @api_bp.route("/check/run", methods=["POST"])
    def run_check():
        """手动触发链接巡检"""
        if checker:
            try:
                checker.check_now()
                return jsonify({"message": "巡检已触发", "stats": checker.get_stats()})
            except Exception as e:
                return jsonify({"error": str(e)}), 500
        return jsonify({"error": "巡检器未启用"}), 400

    # 公开配置 / 公告 / 首页词条（只读，不需要登录）
    from api.public import register_public_routes
    register_public_routes(app, index_db)

    # 需求板（公开提交/列表/投票，受 DEMAND_ENABLED 总开关控制）
    from api.demand import register_demand_routes
    register_demand_routes(app)

    # 后台认证与后台管理（与 ACCEL_TOKEN 分离，受 ADMIN_V2 总开关控制）
    from api.auth import register_auth_routes
    register_auth_routes(app)
    from api.admin import register_admin_routes
    register_admin_routes(app, index_db)

    # 注册蓝图
    app.register_blueprint(api_bp, url_prefix="/api")