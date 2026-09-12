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


class DoubanClient:
    """豆瓣榜单与封面建议客户端"""

    def __init__(self, timeout: float = None):
        self.timeout = timeout or Config.DOUBAN_TIMEOUT
        self.enabled = Config.ENABLE_DOUBAN
        self.last_error = None
        self._hot_cache = {}          # key -> (ts, items)
        self._hot_lock = threading.Lock()

    # ---------- 榜单 ----------

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

    def suggest_poster(self, query: str) -> str | None:
        """
        标题 → 海报 URL（无匹配返回 None）。
        只负责请求与解析，缓存由调用方（poster_cache 表）负责。
        """
        q = (query or "").strip()
        if not q or not self.enabled:
            return None
        try:
            r = requests.get(
                f"{_BASE}/subject_suggest",
                params={"q": q}, headers=DOUBAN_UA, timeout=self.timeout,
            )
            r.raise_for_status()
            for it in (r.json() or []):
                if isinstance(it, dict) and it.get("img"):
                    return it["img"]
            return None
        except Exception as e:
            self.last_error = str(e)
            logger.warning(f"[豆瓣] 封面建议失败 {q[:30]!r}: {e}")
            return None

    # ---------- 图床代理 ----------

    def proxy_image(self, url: str):
        """
        校验豆瓣图床域名后服务端拉图（规避浏览器 Referer 防盗链）。
        返回流式响应对象；非 doubanio.com 域名抛 ValueError（防 SSRF）。
        """
        from urllib.parse import urlparse
        host = urlparse(url or "").netloc.lower()
        if not host.endswith("doubanio.com"):
            raise ValueError("仅支持豆瓣图床(doubanio.com)图片代理")
        r = requests.get(url, headers=DOUBAN_UA,
                         timeout=self.timeout, stream=True)
        r.raise_for_status()
        ct = r.headers.get("Content-Type", "")
        if not ct.startswith("image/"):
            raise ValueError("目标不是图片内容")
        return r
