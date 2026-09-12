"""
douban —— 豆瓣数据客户端（榜单热榜 + 资源封面匹配）

两个公开轻量端点（家用 IP 一般直连可用；被限流时优雅降级为空结果）：
  GET /j/search_subjects  —— 榜单（type=movie/tv × tag=热门/综艺/...）
  GET /j/subject_suggest  —— 按标题模糊匹配条目（给搜索结果取海报封面）

设计：
  - 统一浏览器 UA（豆瓣对 python 默认 UA 风控严格）
  - 榜单内存 TTL 缓存；封面建议结果由调用方落 SQLite poster_cache 持久缓存
  - 任何失败返回空结果，绝不影响搜索主功能
"""
import time
import json
import logging
import threading
import requests
from config import Config

logger = logging.getLogger("douban")

DOUBAN_UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
    "Referer": "https://movie.douban.com/",
}
_BASE = "https://movie.douban.com/j"
# 豆瓣 v2 榜单接口（m.douban.com rexxar）：条目带 card_subtitle
# （年份/国家/类型/导演/主演）与 rating{count,value}，仅需 bid cookie
_M_REXXAR = "https://m.douban.com/rexxar/api/v2"
_REXXAR_HEADERS = {
    **DOUBAN_UA,
    "Referer": "https://m.douban.com/movie",
    "Cookie": "bid=YdDnP8qNblc",
}
# 冷门佳片无独立榜单，走 recommend feed + tags
_GEMS_FEED = "movie_gems"


class DoubanClient:
    """豆瓣榜单与封面建议客户端"""

    def __init__(self, timeout: float = None):
        self.timeout = timeout or Config.DOUBAN_TIMEOUT
        self.enabled = Config.ENABLE_DOUBAN
        self.last_error = None
        self._hot_cache = {}          # key -> (ts, items)
        self._hot_lock = threading.Lock()
        # subject_suggest 熔断：连续空结果说明被豆瓣限流，
        # 冷却期内直接返回 None，不再白打请求
        self._empty_streak = 0
        self._cooldown_until = 0.0

    def cooldown_active(self) -> bool:
        """是否处于 suggest 熔断冷却期（调用方应暂停解析，避免污染空缓存）"""
        return time.time() < self._cooldown_until

    # ---------- v2 榜单（富信息） ----------

    def collection_items(self, collection: str,
                         start: int = 0, count: int = 25) -> list:
        """
        豆瓣榜单条目（v2 subject_collection），规范化为：
          {id, title, card_subtitle, year, pic_normal, pic_large,
           rating_value, rating_count, url, is_new, episodes_info, cover}
        三级回退：subject_collection → recommend feed(仅冷门佳片) → search_subjects。
        """
        collection = (collection or "").strip()[:40] or "movie_hot"
        try:
            if collection == _GEMS_FEED:
                items = self._recommend_feed("电影", "冷门佳片", start, count)
            else:
                r = requests.get(
                    f"{_M_REXXAR}/subject_collection/{collection}/items",
                    params={"start": start, "count": count},
                    headers=_REXXAR_HEADERS, timeout=self.timeout,
                )
                r.raise_for_status()
                raw = r.json().get("subject_collection_items") or []
                items = [self._norm_v2(it) for it in raw if it.get("title")]
            if items:
                self.last_error = None
                return items
        except Exception as e:
            self.last_error = str(e)
            logger.warning(f"[豆瓣] v2 榜单 {collection} 失败: {e}")
        return self._legacy_list(collection, start, count)

    def _norm_v2(self, it: dict) -> dict:
        """v2 条目 → 前端统一契约"""
        rt = it.get("rating") or {}
        rid = str(it.get("id") or "")
        pic = it.get("pic") or {}
        cover = it.get("cover") if isinstance(it.get("cover"), dict) else {}
        cover_url = cover.get("url") or pic.get("large") or pic.get("normal") or ""
        return {
            "id": rid,
            "title": it.get("title") or "",
            "card_subtitle": it.get("card_subtitle") or "",
            "year": str(it.get("year") or ""),
            "pic_normal": pic.get("normal") or "",
            "pic_large": pic.get("large") or "",
            "cover": cover_url,
            "rating_value": rt.get("value") or 0,
            "rating_count": rt.get("count") or 0,
            "url": it.get("url") or (f"https://movie.douban.com/subject/{rid}/" if rid else ""),
            "is_new": bool(it.get("is_new")),
            "episodes_info": it.get("episodes_info") or "",
        }

    def _recommend_feed(self, type_name: str, tag: str,
                        start: int, count: int) -> list:
        """rexxar recommend feed（tags 检索，条目为 subject 卡片）"""
        r = requests.get(
            f"{_M_REXXAR}/movie/recommend",
            params={"refresh": 0, "start": start, "count": count,
                    "selected_categories": json.dumps({"类型": type_name}, ensure_ascii=False),
                    "tags": tag},
            headers=_REXXAR_HEADERS, timeout=self.timeout,
        )
        r.raise_for_status()
        raw = []
        for it in (r.json().get("items") or []):
            if it.get("card") not in (None, "subject"):
                continue  # 过滤豆列/广告卡
            raw.append(it.get("target") or it)
        return [self._norm_v2(it) for it in raw if it.get("title")]

    def _legacy_list(self, collection: str, start: int, count: int) -> list:
        """v2 失败的最终回退：旧版 search_subjects（无富信息，字段降级）"""
        type_ = "tv" if collection.startswith("tv") else "movie"
        tag = {"movie_latest": "最新", "movie_gems": "冷门佳片"}.get(collection, "热门")
        try:
            r = requests.get(
                f"{_BASE}/search_subjects",
                params={"type": type_, "tag": tag,
                        "page_limit": count, "page_start": start},
                headers=DOUBAN_UA, timeout=self.timeout,
            )
            r.raise_for_status()
            subs = (r.json() or {}).get("subjects") or []
            out = []
            for s in subs:
                if not isinstance(s, dict) or not s.get("title"):
                    continue
                out.append({
                    "id": "", "title": s.get("title", ""),
                    "card_subtitle": "", "year": "",
                    "pic_normal": s.get("cover") or "", "pic_large": s.get("cover") or "",
                    "cover": s.get("cover") or "",
                    "rating_value": float(s.get("rate") or 0),
                    "rating_count": 0,
                    "url": s.get("url") or "", "is_new": bool(s.get("is_new")),
                    "episodes_info": "",
                })
            return out
        except Exception as e:
            self.last_error = str(e)
            return []

    # ---------- 榜单（旧版轻量，仍供 /api/douban/hot 兼容） ----------

    def hot(self, type_: str = "movie", tag: str = "热门",
            page_limit: int = 25, page_start: int = 0) -> list:
        """
        获取榜单条目：[{title, rate, url, cover}]
        失败返回 []（last_error 供前端降级提示）。
        """
        if not self.enabled:
            self.last_error = None
            return []
        type_ = type_ if type_ in ("movie", "tv") else "movie"
        tag = (tag or "热门")[:20]
        key = f"{type_}|{tag}|{page_start}"

        with self._hot_lock:
            ent = self._hot_cache.get(key)
            if ent and time.time() - ent[0] < Config.DOUBAN_HOT_TTL_SECONDS:
                return ent[1]

        try:
            r = requests.get(
                f"{_BASE}/search_subjects",
                params={"type": type_, "tag": tag,
                        "page_limit": page_limit, "page_start": page_start},
                headers=DOUBAN_UA, timeout=self.timeout,
            )
            r.raise_for_status()
            subs = (r.json() or {}).get("subjects") or []
            items = [{
                "title": (s.get("title") or "").strip(),
                "rate": (s.get("rate") or "").strip(),
                "url": s.get("url") or "",
                "cover": s.get("cover") or "",
            } for s in subs if isinstance(s, dict) and s.get("title")]
            with self._hot_lock:
                self._hot_cache[key] = (time.time(), items)
            self.last_error = None
            return items
        except Exception as e:
            self.last_error = str(e)
            logger.warning(f"[豆瓣] 榜单获取失败 {type_}/{tag}: {e}")
            return []

    # ---------- 封面建议 ----------

    def _suggest_primary(self, q: str) -> str | None:
        """movie.douban subject_suggest（精确匹配）；限流时熔断 5 分钟只停此端点"""
        now = time.time()
        if now < self._cooldown_until:
            return None
        try:
            r = requests.get(
                f"{_BASE}/subject_suggest",
                params={"q": q}, headers=DOUBAN_UA, timeout=self.timeout,
            )
            r.raise_for_status()
            items = r.json() or []
            if items:
                self._empty_streak = 0
                for it in items:
                    if isinstance(it, dict) and it.get("img"):
                        return it["img"]
                return None
            self._empty_streak += 1
            if self._empty_streak >= 6:
                self._cooldown_until = time.time() + 300
                logger.warning("[豆瓣] subject_suggest 连续空结果，疑似限流，熔断 5 分钟（备胎端点不受影响）")
            return None
        except Exception as e:
            self.last_error = str(e)
            return None

    def _suggest_fallback(self, q: str) -> str | None:
        """www.douban search_suggest 兜底（独立端点，主端点被限流时仍可用）"""
        try:
            r = requests.get(
                "https://www.douban.com/j/search_suggest",
                params={"q": q}, headers=DOUBAN_UA, timeout=self.timeout,
            )
            r.raise_for_status()
            for card in (r.json() or {}).get("cards") or []:
                if isinstance(card, dict) and card.get("cover_url"):
                    return card["cover_url"]
            return None
        except Exception as e:
            self.last_error = str(e)
            return None

    def suggest_poster(self, query: str) -> str | None:
        """
        标题 → 海报 URL（无匹配返回 None）。
        主端点（subject_suggest）被 IP 限流时自动切换备胎端点
        （search_suggest），两者独立限流，几乎不会同时不可用。
        缓存由调用方（poster_cache 表）负责。
        """
        q = (query or "").strip()
        if not q or not self.enabled:
            return None
        img = self._suggest_primary(q)
        if img:
            return img
        return self._suggest_fallback(q)

    # ---------- 图床代理 ----------

    def proxy_image(self, url: str):
        """
        校验白名单域名后服务端拉图（规避防盗链/被墙），返回流式响应对象。
        白名单：豆瓣图床 + Telegram CDN（TG 消息封面图，国内直连不通）。
        豆瓣图床部分边缘节点会返回 JS 挑战，自动轮换 img1-9 子域重试
        （豆瓣各 imgN 子域内容等价）；成功结果进进程内存缓存。
        """
        from urllib.parse import urlparse
        host = urlparse(url or "").netloc.lower()
        allowed = ("doubanio.com", "telesco.pe", "cdn-telegram.org",
                   "telegram.org", "t.me")
        if not any(host == a or host.endswith("." + a) for a in allowed):
            raise ValueError("仅支持豆瓣/Telegram 图床图片代理")

        hit = _IMG_CACHE.get(url)
        if hit:
            return hit

        last_exc = None
        candidates = [url]
        if host.endswith("doubanio.com"):
            import re as _re
            m = _re.match(r"(https://)img(\d)(\.doubanio.com/.+)", url)
            if m:
                prefix, _, rest = m.groups()
                candidates += [f"{prefix}img{n}{rest}"
                               for n in (1, 3, 5, 7, 9) if f"img{n}" != f"img{m.group(2)}"]
        for u in candidates:
            try:
                r = requests.get(u, headers=DOUBAN_UA,
                                 timeout=self.timeout, stream=True)
                r.raise_for_status()
                ct = r.headers.get("Content-Type", "")
                if not ct.startswith("image/"):
                    last_exc = ValueError("目标不是图片内容")
                    continue
                _IMG_CACHE.put(url, ct, r)
                hit = _IMG_CACHE.get(url)
                return hit if hit else r
            except ValueError:
                raise
            except Exception as e:
                last_exc = e
        if last_exc:
            raise last_exc
        raise ValueError("图片拉取失败")


class _ImageMemoryCache:
    """极简图片内存缓存（≤300 张 / 30MB），命中避免重复回源"""

    def __init__(self, max_items=300, max_bytes=30 * 1024 * 1024):
        self._data = {}  # url -> (ct, bytes)
        self._order = []
        self._bytes = 0
        self._lock = threading.Lock()
        self._max_items = max_items
        self._max_bytes = max_bytes

    def get(self, url: str):
        with self._lock:
            ent = self._data.get(url)
            if not ent:
                return None
            ct, body = ent
            import io as _io
            resp = requests.Response()
            resp.status_code = 200
            resp.headers["Content-Type"] = ct
            resp._content = body
            resp.raw = _io.BytesIO(body)
            return resp

    def put(self, url: str, ct: str, resp):
        try:
            body = resp.read()
        except Exception:
            return
        if len(body) > 3 * 1024 * 1024:
            return  # 单图过大不缓存
        with self._lock:
            if url in self._data:
                return
            self._data[url] = (ct, body)
            self._order.append(url)
            self._bytes += len(body)
            while (len(self._order) > self._max_items or
                   self._bytes > self._max_bytes) and self._order:
                old = self._order.pop(0)
                ent = self._data.pop(old, None)
                if ent:
                    self._bytes -= len(ent[1])


_IMG_CACHE = _ImageMemoryCache()
