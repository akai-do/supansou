"""
title_correct —— 中文片名近似纠错（Chrome 式“您是不是要找”）

搜索零结果时，收集候选片名并找出与原查询最接近的写法：
  1. 本地索引已收录标题（毫秒级，但只覆盖站里搜过的资源）
  2. 豆瓣 subject_suggest（一次请求，能补全漏字/标点差异）
  3. PanSou 按“高辨识度片名片段”搜索（较慢，但冷启动时最有效）
     例：“喜洋洋与灰太狼之破界山海诀” → 搜“破界山海诀” →
     结果里出现正确写法“喜羊羊与灰太狼之破界山海诀”，
     再由字符相似度确认。

本模块只负责“提建议”。是否采纳由搜索管线复核决定：纠正后的词必须
真的搜出资源，才会替换原结果并展示“您是不是要找”。这样既能修掉
同音字/漏字，又不会把“确实没有”的查询硬掰成无关资源。
"""
import logging
import re
import threading
import time
from difflib import SequenceMatcher

from search_engine import extract_links_from_result

logger = logging.getLogger("title_correct")

# 零宽字符（资源标题里常见，肉眼不可见但会破坏相似度）
_ZW_RE = re.compile(r"[\u200b-\u200f\u2060\ufeff]")
# 标题开头的 emoji/符号（保留《》「」这类书名号——它们是片名本体）
_LEAD_SYMBOL_RE = re.compile(r"^[^\w《「『]+", re.UNICODE)
# 标题开头的分类标签：资源名称：/电影｜/标题:
_LEAD_LABEL_RE = re.compile(
    r"^(?:资源名称|资源名|标题|名称|片名|影视|电影|电视剧|动漫|动画|"
    r"综艺|纪录片|短剧)\s*(?:名称)?\s*[：:|｜\-]\s*"
)
# 括号包装：(2026)【1.4GB/集】[全60集] —— 《》是正片名的一部分，不删
_BRACKET_RE = re.compile(r"[\(\（\[\【][^\)\）\]\】]{0,60}[\)\）\]\】]")
# 广告/囤积型分隔符
_EMOJI_SPLIT_RE = re.compile(r"[💾💬📁📜👉👇⬇🏷🎬🗄📦🔗]+")
# 画质/集数/版本等包装词：命中后整段截掉
_PACK_TAIL_RE = re.compile(
    r"(?:[\s\-_.·|｜]+)?(?:"
    r"4k|8k|2160p|1080p|720p|hdr10?|remux|blu-?ray|web-?dl|webrip|"
    r"hevc|x26[45]|国英|国语|粤语|中英|中字|字幕|内封|内嵌|外挂|双语|"
    r"简繁|简体|繁体|高清|超清|蓝光|原盘|杜比|视界|全景声|"
    r"全\d+集|\d+集全|更新至\d+集|第[一二三四五六七八九十\d]+[季期]|"
    r"s\d{1,2}(?:e\d{1,3})?|番外篇?|合集|全系列|含大电影|完整版|"
    r"高码率|sdr|剧版|电影版|动画版"
    r")(?=[\s\-_.·|｜]|$).*$",
    re.IGNORECASE,
)
# 片名分词：与/之/的/和 等连接词切段，取最有辨识度的片段去 PanSou 搜
_SEGMENT_SPLIT_RE = re.compile(r"[与和及之的：:，,、·｜|/\\\-_—\s]+")
_GENERIC_SEGMENTS = {
    "电影", "电视剧", "动漫", "动画", "综艺", "纪录片", "短剧",
    "全集", "合集", "全系列", "资源", "高清", "蓝光", "中字",
    "国语", "粤语", "系列", "完整版", "更新", "完结", "番外",
    "番外篇", "剧版", "电影版", "动画版",
}


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= c <= "\u9fff" for c in (text or ""))


def normalize_title(text: str) -> str:
    """只保留汉字/字母/数字，用于字符相似度比较。"""
    t = _ZW_RE.sub("", str(text or "")).lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", t)


def clean_candidate_title(raw: str) -> str:
    """
    资源标题 → 干净的片名。

    “🎬 喜羊羊与灰太狼之破界山海诀‎ (2026) 4K SDR 高码率 中字
      【1.4GB/集】全60集”
    → “喜羊羊与灰太狼之破界山海诀”
    """
    t = _ZW_RE.sub("", str(raw or "")).strip()
    if not t:
        return ""
    # 先去掉开头的 emoji/符号，再在广告 emoji 处截断——
    # 否则“🎬 片名”会被 split 成空串
    t = _LEAD_SYMBOL_RE.sub("", t).strip()
    t = _EMOJI_SPLIT_RE.split(t)[0].strip()
    # 去掉开头的 emoji/符号/分类标签（可能叠两层，循环两次）
    for _ in range(2):
        t = _LEAD_SYMBOL_RE.sub("", t)
        t = _LEAD_LABEL_RE.sub("", t)
    # 去掉括号包装（年份、清晰度、集数、文件大小等）
    t = _BRACKET_RE.sub(" ", t)
    # 去掉画质/集数/合集等尾部包装
    t = _PACK_TAIL_RE.sub("", t)
    # 收尾清理
    t = re.sub(r"\s+", " ", t).strip(" ·-_:：,，.。|｜")
    return t[:60].strip()


def title_score(query: str, candidate: str) -> float:
    """
    两个片名的接近程度（0~1）。

    SequenceMatcher 的 ratio 会偏爱“更长的候选”：正确的 13 字片名
    得 0.923，而带上“番外篇”的 15 字候选反而得 0.929。乘一个长度
    比例后，长度最接近的写法会胜出，避免把“番外篇”推荐成正片。
    """
    a = normalize_title(clean_candidate_title(query))
    b = normalize_title(clean_candidate_title(candidate))
    if not a or not b or a == b:
        return 0.0
    if len(a) < 4 or len(b) < 4:
        return 0.0
    if not (_has_cjk(a) and _has_cjk(b)):
        return 0.0
    len_diff = abs(len(a) - len(b))
    if len_diff > max(2, len(a) // 3):
        return 0.0
    ratio = SequenceMatcher(None, a, b, autojunk=False).ratio()
    length_factor = min(len(a), len(b)) / max(len(a), len(b))
    return ratio * length_factor


def is_near_miss(query: str, candidate: str, threshold: float = None) -> bool:
    """候选是否像“同一个片名的另一种写法”（同音字/漏字/多字）。"""
    a = normalize_title(clean_candidate_title(query))
    b = normalize_title(clean_candidate_title(candidate))
    if not a or not b or a == b:
        return False
    if len(a) < 4 or len(b) < 4:
        return False
    if not (_has_cjk(a) and _has_cjk(b)):
        return False
    if threshold is None:
        # 长片名允许 1-2 个字写错；4-7 字短名要求更严，避免误配
        threshold = 0.84 if len(a) >= 8 else 0.80
    return title_score(query, candidate) >= threshold


def pick_near_miss(query: str, candidates, limit: int = 3) -> list:
    """从一堆资源标题里挑出最像原查询的几个干净片名。"""
    query_key = normalize_title(clean_candidate_title(query))
    scored = {}
    for raw in candidates:
        title = clean_candidate_title(raw)
        if not title:
            continue
        key = normalize_title(title)
        if not key or key == query_key:
            continue
        if not is_near_miss(query, title):
            continue
        score = title_score(query, title)
        old = scored.get(key)
        # 同一写法被多个发布帖重复搬运时，保留分数最高（通常信息最全）的
        if old is None or score > old[0]:
            scored[key] = (score, title)
    ranked = sorted(scored.values(), key=lambda x: (-x[0], len(x[1])))
    return [t for _, t in ranked[:limit]]


def cjk_segments(keyword: str, max_segments: int = 3) -> list:
    """
    长片名 → 高辨识度片段（用于在零结果时绕开错别字去 PanSou 捞正确写法）。

    “喜洋洋与灰太狼之破界山海诀” → [破界山海诀, 喜洋洋, 灰太狼]
    片段按长度降序：越长越具体，越不容易被海量无关结果淹没。
    """
    raw = _ZW_RE.sub("", str(keyword or ""))
    parts = _SEGMENT_SPLIT_RE.split(raw)
    out = []
    for part in parts:
        p = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", part)
        if not p or not _has_cjk(p) or p in _GENERIC_SEGMENTS:
            continue
        variants = [p]
        if len(p) > 12:
            # 超长且没有连接词可切：取首尾各 6 字，尾部通常是作品名主体
            variants = [p[-6:], p[:6], p]
        for v in variants:
            if v and v not in _GENERIC_SEGMENTS and v not in out:
                out.append(v)
    # 稳定排序：长度降序（Python sort 稳定，同长度保持原顺序）
    out.sort(key=len, reverse=True)
    return out[:max_segments]


class TitleCorrector:
    """带缓存的片名纠错器（零结果路径专用，每次建议只算一次）"""

    def __init__(self, ttl: int = 600, negative_ttl: int = 120,
                 max_entries: int = 200):
        self._cache = {}
        self._lock = threading.Lock()
        self._ttl = ttl
        self._negative_ttl = negative_ttl
        self._max = max_entries

    def _cache_get(self, key: str):
        now = time.time()
        with self._lock:
            ent = self._cache.get(key)
            if ent:
                # 负结果只缓存 2 分钟：源站抖动/限流导致的“没找到”
                # 不应该把用户锁在错误片名上太久。
                ttl = self._ttl if ent[1] else self._negative_ttl
                if now - ent[0] < ttl:
                    return list(ent[1])
        return None

    def _cache_put(self, key: str, value: list):
        with self._lock:
            if len(self._cache) >= self._max:
                stale = sorted(self._cache.items(), key=lambda kv: kv[1][0])
                for k, _ in stale[:self._max // 2]:
                    self._cache.pop(k, None)
            self._cache[key] = (time.time(), list(value))

    def suggest(self, keyword: str, pansou_client=None, index_db=None,
                douban_client=None, deadline: float = None) -> list:
        """
        返回按可信度排序的候选片名（最多 3 个）；无需纠正返回 []。

        deadline 是流式搜索的硬截止时间：临近截止时不再发起 PanSou
        片段搜索，把剩余时间留给真正要展示给用户的纠正后搜索。
        """
        kw = (keyword or "").strip()
        if len(kw) < 4 or not _has_cjk(kw):
            return []
        cache_key = normalize_title(kw)
        if not cache_key:
            return []
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        candidates = []

        # 1) 本地索引：搜过就会被“渐进式索引”收进来，零成本
        if index_db is not None:
            try:
                rows = index_db.search(kw, limit=80)
                candidates.extend(str(r.get("title") or "") for r in rows)
            except Exception as e:
                logger.warning(f"[片名纠错] 索引候选失败: {e}")

        # 2) 豆瓣：处理漏字/标点差异（同音字它不认，所以只是补充）
        if douban_client is not None:
            try:
                if (getattr(douban_client, "enabled", False)
                        and not douban_client.cooldown_active()):
                    for it in douban_client.suggest_titles(kw):
                        candidates.append(it.get("title") or "")
            except Exception as e:
                logger.warning(f"[片名纠错] 豆瓣候选失败: {e}")

        # 如果候选里已经有“去掉标点/包装后与原词完全相同”的写法，
        # 说明用户没写错字（只是少了冒号之类），原搜索本身能处理，
        # 不必再花十几秒做 PanSou 片段搜索。
        query_key = normalize_title(clean_candidate_title(kw))
        if query_key and any(
                normalize_title(clean_candidate_title(c)) == query_key
                for c in candidates):
            self._cache_put(cache_key, [])
            return []

        picked = pick_near_miss(kw, candidates)

        # 2.5) 本地索引片段搜索：全词因错别字搜不到时，用“破界山海诀”
        #      这类高辨识度片段去索引里捞。SQLite 毫秒级，而且不依赖
        #      PanSou 的网络状态——只要这个资源以前被任何人搜到过就能纠。
        if not picked and index_db is not None:
            for segment in cjk_segments(kw):
                if normalize_title(segment) == query_key:
                    continue
                try:
                    rows = index_db.search(segment, limit=80)
                    candidates.extend(str(r.get("title") or "") for r in rows)
                except Exception as e:
                    logger.warning(f"[片名纠错] 索引片段失败 {segment!r}: {e}")
                    continue
                picked = pick_near_miss(kw, candidates)
                if picked:
                    break

        # 3) PanSou 片段搜索：错别字把整词搜死时，用片名片段把正确写法捞回来
        if not picked and pansou_client is not None:
            query_norm = normalize_title(kw)
            for segment in cjk_segments(kw):
                # 单段片名没有可拆的片段时，segs[0] 就是原词；
                # 原词刚才已经搜过且零结果，再搜一遍纯属浪费十几秒。
                if normalize_title(segment) == query_norm:
                    continue
                if deadline is not None and time.time() > deadline - 12:
                    break
                try:
                    resp = pansou_client.search(segment, result_type="all")
                    if resp.get("error"):
                        continue
                    links = extract_links_from_result(resp)
                    candidates.extend(
                        str(ln.get("title") or "") for ln in links[:800])
                except Exception as e:
                    logger.warning(f"[片名纠错] 片段搜索失败 {segment!r}: {e}")
                    continue
                picked = pick_near_miss(kw, candidates)
                if picked:
                    break

        if picked:
            logger.info(f"[片名纠错] '{kw}' 候选: {picked}")
        self._cache_put(cache_key, picked)
        return picked


_title_corrector = TitleCorrector()


def suggest_title_corrections(keyword: str, pansou_client=None, index_db=None,
                              douban_client=None, deadline: float = None) -> list:
    """模块级便捷入口（搜索管线直接调用）"""
    return _title_corrector.suggest(
        keyword, pansou_client=pansou_client, index_db=index_db,
        douban_client=douban_client, deadline=deadline,
    )
