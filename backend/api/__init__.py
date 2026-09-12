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
import re
import time
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from flask import Blueprint, request, jsonify, redirect, Response
from datetime import datetime
from config import Config
from search_engine.xuebapan import xuebapan_client as _xuebapan
from search_engine.spell import suggest_correction as _suggest_correction

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


def _cache_put(key: str, payload: dict):
    """写入缓存；超容量时淘汰最旧的一半"""
    with _search_cache_lock:
        if len(_search_cache) >= _SEARCH_CACHE_MAX:
            stale = sorted(_search_cache.items(), key=lambda kv: kv[1][0])
            for k, _ in stale[:_SEARCH_CACHE_MAX // 2]:
                _search_cache.pop(k, None)
        _search_cache[key] = (time.time(), payload)


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



def _matches_query(item, keyword, tokens):
    """
    判定一条实时结果与查询是否真正相关（而非仅被某个子词顺带命中）。

    用于过滤掉"变体搜索"带回来的偏差结果，例如搜"蜘蛛侠"时，
    PanSou 对"电影"子词返回的海量与本查询无关的影视链接。
    注意：子词只匹配标题/来源——网盘分享码是随机字符串，
    若参与匹配，垃圾查询会因分享码恰好含子串而漏进无关结果。

    长尾兜底由调用方控制：tokens 用 _tokenize_query(kw, extended=True)
    生成的扩展表（含 CJK 信息词）即宽松模式，仅在严格过滤颗粒无收时使用。
    """
    title_source = " ".join([
        str(item.get("title", "") or ""),
        str(item.get("source", "") or ""),
    ]).lower()
    full_blob = title_source + " " + str(item.get("url", "") or "").lower()
    if not tokens:
        return keyword.lower() in full_blob
    # 完整原词命中（标题/来源/URL）即合格
    if keyword and keyword.lower() in full_blob:
        return True
    # 否则需命中任一有意义的信息词（仅标题/来源）
    return any(t in title_source for t in tokens)


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
    return sum(1 for t in tokens if t in title_source)


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
        t = t[:maxlen].rstrip(" ·：:,，. ") + "…"
    return t


def _dedup_and_rank(archive_items, realtime_items, keyword, click_map=None):
    """
    融合历史索引与实时搜索结果：跨源去重 + 综合排序。

    规则：
      1. 以 URL 为标准去重（规范化：去协议/去末尾斜杠/解码）
      2. 历史与实时撞车时：保留实时结果（数据更新、带完整来源），
         但若历史结果带密码而实时不带，则补全密码
      3. 综合评分 = 相关度(标题/来源命中) + 存活(实时优先) + 网盘偏好
                      + 点击热度(本关键词下被点过的资源加权置顶)
    返回 (完整融合排序列表, 历史计数, 实时计数)。
    截断交给上层的 _group_resources（按资源分组后再限量）。
    """
    def norm_url(u):
        if not u:
            return u
        u = u.strip()
        u = re.sub(r'^[a-z]+://', '', u, flags=re.IGNORECASE)
        u = u.rstrip('/')
        try:
            from urllib.parse import unquote
            u = unquote(u)
        except Exception:
            pass
        return u.lower()

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
        return s

    merged = {}
    history = 0
    realtime = 0

    def upsert(item, is_realtime):
        key = norm_url(item.get("url", ""))
        if not key:
            return
        it = dict(item)
        # 精简实时广告式标题；历史索引入库时已精简，这里冗余处理以保证一致
        if isinstance(it.get("title"), str):
            it["title"] = _shorten_title(it["title"])
        existing = merged.get(key)
        if existing is None:
            merged[key] = it
            return
        # 撞车处理
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
}
_QUALITY_PAT = re.compile(
    r"^(h\.?26[45]|x26[45]|2160p|1080p|720p|480p|web-?dl|blu-?ray|"
    r"\d+bit|全\d+集|\d+集全|更新至\d+集)$"
)
# 注意：季数（第X季/S01）不剔除——季与季是不同资源，不能合并


def _normalize_title_key(title: str) -> str:
    """标题 → 归一化分组键：去噪后仅保留字母/数字/汉字"""
    t = (title or "").lower()
    t = re.sub(r"[\[\]【】()（）{}<>《》「」『』|·:：;；,，。.?？!！~～\"'“”‘’]",
               " ", t)
    tokens = t.split()
    keep = [tok for tok in tokens
            if tok not in _QUALITY_TOKENS and not _QUALITY_PAT.match(tok)]
    key = "".join(keep)
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", key)


def _rep_score(it: dict) -> float:
    """组内选主卡：有效性 > 带密码 > 实时 > 标题更完整 > 网盘偏好"""
    s = 0.0
    v = it.get("validity") or ""
    if v == "ok":
        s += 2.5
    elif v == "dead" or it.get("alive", 1) == 0:
        s -= 5.0
    elif v == "suspect":
        s -= 1.5
    else:
        s += 2.0  # 未检测的实时结果默认存活
    if it.get("password"):
        s += 1.0
    if not it.get("from_archive"):
        s += 0.5
    s += min(len(str(it.get("title") or "")), 60) / 200.0
    s += -0.05 * DISK_PRIORITY.get(str(it.get("disk_type", "")).lower(), 10)
    return s


_VARIANT_FIELDS = ("url", "password", "disk_type", "source",
                   "datetime", "last_seen", "alive", "from_archive", "title",
                   "validity", "state_summary", "checked_at", "msg_image")


def _group_resources(ranked: list, limit: int = 60) -> list:
    """
    把融合排序后的平铺链接聚合成"资源卡片"列表。

    - 归一化标题一致（且键长>=3，避免误合并短名资源）→ 同组
    - 无标题或键过短的条目按 URL 独立成卡，不参与聚合
    - 每组产出一张主卡：保留主链接全部字段，其余链接进 variants
    - 组间顺序沿用原相关性排序（组内重选主卡不影响组序）
    """
    groups = {}
    order = []
    for it in ranked:
        key = _normalize_title_key(it.get("title"))
        if key and len(key) >= 3:
            gk = "t:" + key
        else:
            gk = "u:" + str(it.get("url", ""))
        if gk not in groups:
            groups[gk] = [it]
            order.append(gk)
        else:
            groups[gk].append(it)

    cards = []
    for gk in order:
        members = groups[gk]
        if len(members) == 1:
            cards.append(dict(members[0]))
            continue
        rep = max(members, key=_rep_score)
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
    prefix = kw + "|"
    with _search_cache_lock:
        for k in [k for k in _search_cache if k.startswith(prefix)]:
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


def _background_smart_check(checker, cards: list, cache_key: str):
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
                _invalidate_kw_cache(cache_key.split("|")[0])
                logger.info(f"[智能检测] 搜索后补检 {len(items)} 条，"
                            f"有新结论已刷新缓存")
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
            "only_archive": false,       // 可选，只搜历史索引（不调 PanSou）
            "src": "all",               // 可选，PanSou 的 src 参数
            "force_refresh": false       // 可选，强制刷新（跳过缓存）
        }
        """
        data = request.get_json(force=True, silent=True) or {}
        keyword = data.get("kw", "").strip()
        if not keyword:
            return jsonify({"error": "关键词不能为空"}), 400

        disk_types = data.get("disk_types")
        only_archive = data.get("only_archive", False)
        force_refresh = data.get("force_refresh", False)
        try:
            page = max(1, int(data.get("page", 1) or 1))
        except (TypeError, ValueError):
            page = 1
        try:
            page_size = min(60, max(5, int(data.get("page_size", 20) or 20)))
        except (TypeError, ValueError):
            page_size = 20

        def _do_search(kw):
            """执行一次完整搜索（缓存 get/put、历史记录按词内部处理）。返回 (payload, from_cache)。"""
            cache_key = "|".join([
                kw.lower(),
                "a" if only_archive else "r",
                ",".join(sorted(disk_types)) if disk_types else "-",
                str(data.get("src", "all")),
            ])
            payload = None
            if not force_refresh:
                with _search_cache_lock:
                    ent = _search_cache.get(cache_key)
                    if ent and time.time() - ent[0] < Config.CACHE_TTL_SECONDS:
                        payload = ent[1]
            if payload is not None:
                return payload, True

            # ====== 第 1 路：从本地索引库搜索（历史结果）======
            archive_results = []
            try:
                archive_results = index_db.search(
                    kw, disk_types=disk_types, limit=50
                )
            except Exception as e:
                logger.error(f"索引搜索失败: {e}")

            # ====== 第 2 路：实时搜索（PanSou 多变体 + 学霸盘书源）======
            # 主变体+补充变体+书源一起并发扇出；补充结果必须含原词才能入选，
            # 只扩召回、不污染主查询的相关性。
            realtime_results = []
            pansou_error = None
            if not only_archive:
                try:
                    variants = _build_search_variants(kw)
                    supplements = _build_supplement_variants(kw)
                    # 严格相关度过滤的信息词（仅 ASCII；长尾兜底时换扩展表）
                    query_tokens = _tokenize_query(kw)
                    import threading as _th

                    def _run_wave(query_list, with_xuebapan=False):
                        """并发执行一批查询（可附带学霸盘书源），返回 collected"""
                        collected = []
                        lock = _th.Lock()

                        def _one_pansou(variant):
                            try:
                                resp = pansou_client.search(
                                    variant,
                                    src=data.get("src", "all"),
                                    plugins=data.get("plugins"),
                                    cloud_types=disk_types,
                                    force_refresh=force_refresh,
                                    result_type="all",
                                )
                                with lock:
                                    if resp.get("error"):
                                        collected.append(("err", resp.get("message")))
                                    else:
                                        collected.append(("ok", variant,
                                                          searcher.extract_links(resp)))
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

                        threads = [_th.Thread(target=_one_pansou, args=(v,), daemon=True)
                                   for v in query_list]
                        if with_xuebapan:
                            threads.append(_th.Thread(target=_one_xuebapan, daemon=True))
                        for t in threads:
                            t.start()
                        for t in threads:
                            t.join(timeout=max(Config.PANSOU_TIMEOUT + 5,
                                               Config.XUEBAPAN_TIMEOUT + 5))
                        return collected

                    def _absorb(collected, tokens):
                        """合并一批结果进 merged_realtime（相关度过滤 + 智能去重）"""
                        nonlocal pansou_error
                        for item in collected:
                            if item[0] != "ok":
                                if not pansou_error:
                                    pansou_error = item[1]
                                continue
                            _, _variant_key, links = item
                            for ln in links:
                                u = ln.get("url", "")
                                if not u:
                                    continue
                                # 网盘类型过滤（学霸盘书源不走 PanSou 的
                                # cloud_types 参数，这里统一补一道）
                                if disk_types and \
                                        str(ln.get("disk_type") or "").lower() not in disk_types:
                                    continue
                                # ★ 相关性过滤：剔除"子词/修饰词顺带命中"但与原查询无关的结果，
                                #   这是把"一个都没有"提升到"一排都是可用结果"的关键。
                                if not _matches_query(ln, kw, tokens):
                                    continue
                                old = merged_realtime.get(u)
                                if old is None or _link_quality(ln) > _link_quality(old):
                                    merged_realtime[u] = ln

                    merged_realtime = {}
                    combined = _run_wave(variants + supplements, with_xuebapan=True)
                    _absorb(combined, query_tokens)

                    # ★ 低召回重查：吃满 PanSou 异步爬取带来的服务端缓存增长
                    retries = 0
                    while (0 < len(merged_realtime) < Config.RECALL_REQUERY_THRESHOLD
                           and retries < Config.RECALL_REQUERY_MAX):
                        time.sleep(Config.RECALL_REQUERY_WAIT)
                        _absorb(_run_wave(variants), query_tokens)
                        retries += 1

                    # ★ 长尾兜底：严格过滤颗粒无收时，用扩展信息词（含 CJK 段）
                    #   重滤同一批结果——长书名/教材名常被"全名必须出现"误杀。
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
                            old = merged_realtime.get(u)
                            if old is None or _link_quality(ln) > _link_quality(old):
                                merged_realtime[u] = ln
                        if merged_realtime:
                            logger.info(f"[长尾] '{kw[:40]}' 宽松兜底命中 {len(merged_realtime)} 条")

                    realtime_results = list(merged_realtime.values())

                    # ★★★ 将实时搜索结果写入索引库 ★★★
                    # "渐进式索引"的核心 —— 每次搜索的结果都存入索引
                    try:
                        stored = index_db.store_links(realtime_results)
                        if stored > 0:
                            logger.info(f"[Search] '{kw}' → 索引新增 {stored} 条")
                    except Exception as e:
                        logger.error(f"写入索引失败: {e}")

                    # ★ 用索引库的智能检测状态给实时结果补上有效性：
                    #   早被巡检/举报确认失效的链接，不能因为"实时"就当存活展示
                    try:
                        _vmap = index_db.get_validity_map(
                            [ln.get("url") for ln in realtime_results]
                        )
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
            for item in archive_results:
                item["from_archive"] = True
            for item in realtime_results:
                item["from_archive"] = False

            # ★★★ 跨源去重 + 综合排序 + 同资源聚合 + 点击热度置顶 ★★★
            try:
                click_map = index_db.get_click_stats(kw)
            except Exception as e:
                logger.error(f"读取点击统计失败: {e}")
                click_map = {}
            ranked_pool, hist_n, real_n = _dedup_and_rank(
                archive_results, realtime_results, kw, click_map=click_map
            )
            cards = _group_resources(ranked_pool, limit=None)

            # ★ 智能隐藏：全部链接确认失效的资源卡移出主列表
            #   （hidden_dead 随响应返回，前端可展开"已隐藏的失效资源"）
            hidden_dead_cards = []
            if Config.HIDE_DEAD_LINKS:
                visible_cards = []
                for c in cards:
                    if _card_validity(c) == "dead":
                        hidden_dead_cards.append(c)
                    else:
                        visible_cards.append(c)
                if hidden_dead_cards:
                    logger.info(f"[智能检测] '{kw}' 隐藏 {len(hidden_dead_cards)} "
                                f"张失效资源卡")
                cards = visible_cards

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

            payload = {
                "cards": cards,
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
                    _background_smart_check(checker, cards, cache_key)
                except Exception as e:
                    logger.error(f"后台补检调度失败: {e}")
            return payload, False

        payload, from_cache = _do_search(keyword)

        # ★ 拼写纠错：0 结果时自动纠正重搜（Chrome 式"您是不是要找"）
        #   注：不看 pansou_error——单个变体失败不应阻止纠错
        correction = None
        if not payload["cards"] and not only_archive:
            sug = _suggest_correction(index_db, keyword)
            if sug and sug.lower() != keyword.lower():
                p2, fc2 = _do_search(sug)
                if p2["cards"]:
                    payload, from_cache = p2, fc2
                    correction = {"from": keyword, "to": sug}
                    logger.info(f"[纠错] '{keyword}' → '{sug}' ({len(p2['cards'])} 张资源卡)")

        # ★ 零结果守望队列：真没有的词入队，harvester 每轮全源重试
        if not payload["cards"]:
            try:
                index_db.record_zero_result(keyword)
                logger.info(f"[守望] '{keyword}' 零结果，已加入守望队列")
            except Exception as e:
                logger.error(f"记录守望队列失败: {e}")

        cards = payload["cards"]
        raw_count = payload["raw_count"]
        hist_n = payload["hist"]
        real_n = payload["real"]
        archive_raw = payload["archive_raw"]
        realtime_raw = payload["realtime_raw"]
        hidden_dead_cards = payload.get("hidden_dead") or []
        pansou_error = payload["error"]

        # ====== 分页切片（缓存命中与新搜索共用）======
        total_cards = len(cards)
        total_pages = max(1, (total_cards + page_size - 1) // page_size)
        if page > total_pages:
            page = total_pages
        start = (page - 1) * page_size
        page_cards = cards[start:start + page_size]

        return jsonify({
            "keyword": keyword,
            "effective_keyword": (correction or {}).get("to", keyword),
            "correction": correction,
            "total": total_cards,
            "raw_count": raw_count,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "cached": from_cache,
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
        })

    @api_bp.route("/history", methods=["GET"])
    def get_history():
        """搜索历史列表（默认最近 20 条）"""
        try:
            limit = min(100, max(1, int(request.args.get("limit", "20"))))
        except (TypeError, ValueError):
            limit = 20
        return jsonify({"history": index_db.get_history(limit)})

    @api_bp.route("/history", methods=["DELETE"])
    def delete_history():
        """删除搜索历史：?kw=xxx 删单条，不传 kw 清空全部"""
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
        """
        target = request.args.get("u", "").strip()
        kw = request.args.get("kw", "").strip()
        if not target or not re.match(r"^https?://", target, re.IGNORECASE):
            return jsonify({"error": "仅支持 http(s) 链接中转"}), 400

        try:
            index_db.record_click(target, kw)
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
            prefix = kw.lower() + "|"
            with _search_cache_lock:
                for k in [k for k in _search_cache if k.startswith(prefix)]:
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
                results = index_db.search(
                    keyword, disk_types=disk_types,
                    limit=limit, only_alive=only_alive
                )
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
            harvester_stats = searcher.harvester.get_stats() if hasattr(searcher, 'harvester') else {}
            checker_stats = checker.get_stats() if checker else {}

            return jsonify({
                "index": index_stats,
                "analyzer": analyzer_stats,
                "harvester": harvester_stats,
                "checker": checker_stats,
            })
        except Exception as e:
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
        """豆瓣图床代理：/api/poster/img?u=<doubanio URL>，规避 Referer 防盗链"""
        u = request.args.get("u", "").strip()
        try:
            r = douban_client.proxy_image(u)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": f"拉取失败: {e}"}), 502

        def _stream():
            try:
                for chunk in r.iter_content(8192):
                    if chunk:
                        yield chunk
            finally:
                r.close()

        resp = Response(_stream(),
                        content_type=r.headers.get("Content-Type", "image/jpeg"))
        resp.headers["Cache-Control"] = "public, max-age=604800"
        return resp

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

    # 注册蓝图
    app.register_blueprint(api_bp, url_prefix="/api")