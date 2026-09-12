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
import threading
from flask import Blueprint, request, jsonify
from datetime import datetime
from config import Config

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
    return variants[:6]


def _build_supplement_variants(keyword: str) -> list:
    """
    补充变体：给主关键词追加通用修饰词扩搜（如 "python 教程"、"海贼王 合集"）。
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
        q = f"{kw} {m}"
        if q not in out:
            out.append(q)
    return out[:3]


def _link_quality(it: dict) -> float:
    """URL 撞车时选信息更全的一条：带密码 > 标题完整 > 有时间"""
    q = 0.0
    if it.get("password"):
        q += 2.0
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
    # 否则需命中任一有意义的子词（仅标题/来源）
    return any(t in title_source for t in tokens)


def _tokenize_query(keyword: str) -> list:
    """提取用于相关性过滤的有意义子词（与 _split_terms 同步）"""
    tokens = []
    ascii_buf = []

    def flush():
        if ascii_buf:
            w = "".join(ascii_buf).lower()
            if not re.sub(r"[\d\s]", "", w) == "" and len(w) >= 2 and w not in tokens:
                tokens.append(w)
            ascii_buf.clear()

    for c in list(keyword):
        if ord(c) < 128:
            ascii_buf.append(c)
            continue
        flush()
    flush()
    return tokens


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


def _dedup_and_rank(archive_items, realtime_items, keyword):
    """
    融合历史索引与实时搜索结果：跨源去重 + 综合排序。

    规则：
      1. 以 URL 为标准去重（规范化：去协议/去末尾斜杠/解码）
      2. 历史与实时撞车时：保留实时结果（数据更新、带完整来源），
         但若历史结果带密码而实时不带，则补全密码
      3. 综合评分 = 相关度(标题/来源命中) + 存活(实时优先) + 网盘偏好
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
        # 存活（实时结果视为最新，默认存活）
        alive = item.get("alive", 1) if item.get("from_archive") else 1
        s += 2.0 if alive else -5.0
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
    """组内选主卡：存活 > 带密码 > 实时 > 标题更完整 > 网盘偏好"""
    s = 0.0
    s += 2.0 if it.get("alive", 1) else -5.0
    if it.get("password"):
        s += 1.0
    if not it.get("from_archive"):
        s += 0.5
    s += min(len(str(it.get("title") or "")), 60) / 200.0
    s += -0.05 * DISK_PRIORITY.get(str(it.get("disk_type", "")).lower(), 10)
    return s


_VARIANT_FIELDS = ("url", "password", "disk_type", "source",
                   "datetime", "last_seen", "alive", "from_archive", "title")


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
        card["variants"] = [
            {k: m.get(k) for k in _VARIANT_FIELDS}
            for m in members if m is not rep
        ]
        card["variant_count"] = len(card["variants"])
        cards.append(card)
    return cards if limit is None else cards[:limit]


def register_routes(app, searcher, index_db, checker, analyzer, pansou_client):
    """注册所有路由（由 app.py 调用注入依赖）"""

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

        # ====== 结果缓存：TTL 内同关键词翻页零成本 ======
        cache_key = "|".join([
            keyword.lower(),
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
        from_cache = payload is not None

        if from_cache:
            cards = payload["cards"]
            hist_n = payload["hist"]
            real_n = payload["real"]
            raw_count = payload["raw_count"]
            archive_raw = payload["archive_raw"]
            realtime_raw = payload["realtime_raw"]
            pansou_error = payload["error"]
        else:
            # ====== 第 1 路：从本地索引库搜索（历史结果）======
            archive_results = []
            try:
                archive_results = index_db.search(
                    keyword, disk_types=disk_types, limit=50
                )
            except Exception as e:
                logger.error(f"索引搜索失败: {e}")

            # ====== 第 2 路：从 PanSou 实时搜索（最新结果）======
            # 多变体并发：PanSou 对连续中文词组（如"蜘蛛侠电影"）匹配极弱，
            # 仅返回个位数。这里自动拆词生成多个搜索变体并发请求，合并去重，
            # 大幅提升实时召回率（这是"搜得新"的关键手段）。
            realtime_results = []
            pansou_error = None
            if not only_archive:
                try:
                    variants = _build_search_variants(keyword)
                    supplements = _build_supplement_variants(keyword)
                    # 用于相关度过滤的有意义子词（对"变体搜索"带回的偏差结果做二次筛选）
                    query_tokens = _tokenize_query(keyword)
                    import threading as _th

                    def _run_wave(query_list):
                        """并发执行一批查询，返回原始 collected 列表"""
                        collected = []
                        lock = _th.Lock()

                        def _one(variant):
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

                        threads = [_th.Thread(target=_one, args=(v,), daemon=True)
                                   for v in query_list]
                        for t in threads:
                            t.start()
                        for t in threads:
                            t.join(timeout=Config.PANSOU_TIMEOUT + 5)
                        return collected

                    def _absorb(collected):
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
                                # ★ 相关性过滤：剔除"子词/修饰词顺带命中"但与原查询无关的结果，
                                #   这是把"一个都没有"提升到"一排都是可用结果"的关键。
                                if not _matches_query(ln, keyword, query_tokens):
                                    continue
                                old = merged_realtime.get(u)
                                if old is None or _link_quality(ln) > _link_quality(old):
                                    merged_realtime[u] = ln

                    merged_realtime = {}
                    # 主变体 + 补充变体一起扇出：补充结果必须含原词才能入选，
                    # 因此只扩召回、不污染主查询的相关性。
                    _absorb(_run_wave(variants + supplements))

                    # ★ 低召回重查：吃满 PanSou 异步爬取带来的服务端缓存增长。
                    # 仅当"有少量相关结果"时才重查（冷词正在后台爬）；
                    # 完全无相关结果说明是垃圾/极冷查询，重查同样查询词无意义。
                    retries = 0
                    while (0 < len(merged_realtime) < Config.RECALL_REQUERY_THRESHOLD
                           and retries < Config.RECALL_REQUERY_MAX):
                        time.sleep(Config.RECALL_REQUERY_WAIT)
                        _absorb(_run_wave(variants))
                        retries += 1

                    realtime_results = list(merged_realtime.values())

                    # ★★★ 将实时搜索结果写入索引库 ★★★
                    # "渐进式索引"的核心 —— 每次搜索的结果都存入索引
                    try:
                        stored = index_db.store_links(realtime_results)
                        if stored > 0:
                            logger.info(f"[Search] '{keyword}' → 索引新增 {stored} 条")
                    except Exception as e:
                        logger.error(f"写入索引失败: {e}")

                except Exception as e:
                    pansou_error = str(e)
                    logger.error(f"PanSou 搜索失败: {e}")

            # ====== 融合结果 ======
            # 历史结果标记来源
            for item in archive_results:
                item["from_archive"] = True

            # 实时结果标记
            for item in realtime_results:
                item["from_archive"] = False

            # ★★★ 跨源去重 + 综合排序 + 同资源聚合 ★★★
            # 融合成一条结果流后，按归一化标题把同一资源的多个
            # 网盘/发布帖合并为一张资源卡（variants 可展开）。
            ranked_pool, hist_n, real_n = _dedup_and_rank(
                archive_results, realtime_results, keyword
            )
            cards = _group_resources(ranked_pool, limit=None)
            raw_count = len(ranked_pool)
            archive_raw = len(archive_results)
            realtime_raw = len(realtime_results)

            # ★ 记录搜索历史（仅真实搜索计数，缓存翻页不计）
            try:
                index_db.record_search(keyword)
            except Exception as e:
                logger.error(f"记录搜索历史失败: {e}")

            payload = {
                "cards": cards,
                "raw_count": raw_count,
                "hist": hist_n,
                "real": real_n,
                "archive_raw": archive_raw,
                "realtime_raw": realtime_raw,
                "error": pansou_error,
            }
            _cache_put(cache_key, payload)

        # ====== 分页切片（缓存命中与新搜索共用）======
        total_cards = len(cards)
        total_pages = max(1, (total_cards + page_size - 1) // page_size)
        if page > total_pages:
            page = total_pages
        start = (page - 1) * page_size
        page_cards = cards[start:start + page_size]

        return jsonify({
            "keyword": keyword,
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