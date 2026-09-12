"""
xuebapan —— 学霸盘书源适配器

学霸盘（xuebapan.com）是学习资料/书籍向的百度网盘搜索站：
  - 检索：GET /s/{关键词}-{页码}.html（路径式 URL，服务端渲染 HTML）
  - 结果：<h3 class="resource-title"><a class="valid" href="/info/{hex}.html">标题</a></h3>
    （class="valid" 表示链接存活；detail-wrap 里有文件清单和大小）
  - 详情页：/info/{hex}.html 含明文提取码 <span id="pwd">XXXX</span>
  - 真实网盘链接在 /goto/ 页由加密 JS 生成（服务端无法解析），
    因此适配器把结果 URL 指向详情页，用户浏览器完成最后一跳。

健壮性：任何异常静默降级返回空列表，不影响主搜索流程；
ENABLE_XUEBAPAN=false 可整体关闭。
"""
import re
import html as html_lib
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import requests

from config import Config

logger = logging.getLogger("xuebapan")

_BASE = "https://www.xuebapan.com"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# 列表页条目：h3 标题块（含 valid/invalid class + /info/ 链接 + 标题文本）
_ITEM_RE = re.compile(
    r'<h3 class="resource-title"><a class="([^"]*)" href="(/info/[0-9a-f]{32}\.html)"[^>]*>(.*?)</a></h3>',
    re.S,
)
_PWD_RE = re.compile(r'<span id="pwd">([^<]{1,20})</span>')
_DATE_RE = re.compile(r'\d{4}-\d{2}-\d{2}')
_SIZE_RE = re.compile(r'([0-9.]+)\s*(MB|GB|KB)', re.I)
_MARK_RE = re.compile(r'</?mark>')


def _clean_title(raw: str) -> str:
    t = _MARK_RE.sub("", raw or "")
    t = html_lib.unescape(t)
    return re.sub(r"\s+", " ", t).strip()


class XuebapanClient:
    """学霸盘搜索客户端（详情页直链策略）"""

    def __init__(self, timeout: int = None, max_items: int = None,
                 max_detail_fetch: int = 3):
        self.timeout = timeout or Config.XUEBAPAN_TIMEOUT
        self.max_items = max_items or Config.XUEBAPAN_MAX_ITEMS
        self.max_detail_fetch = max_detail_fetch
        self._session = requests.Session()
        # 直连：书站在国内可达，不走系统代理（FlClash 等），避免代理抖动
        self._session.trust_env = False
        self._session.headers.update({"User-Agent": _UA})

    @property
    def enabled(self) -> bool:
        return bool(Config.ENABLE_XUEBAPAN)

    def search(self, keyword: str) -> list:
        """
        搜索学霸盘，返回与主融合管线同构的链接列表：
        [{"url", "password", "title", "disk_type", "source", "datetime"}]
        任何失败返回 []。
        """
        kw = (keyword or "").strip()
        if not kw or not self.enabled:
            return []
        try:
            items = self._search_list(kw)
        except Exception as e:
            logger.warning(f"列表页失败: {e}")
            return []
        if not items:
            return []

        # 详情页提取码：只并发取前 N 条（礼貌抓取），其余密码留空（详情页可见）
        top = items[:self.max_detail_fetch]
        with ThreadPoolExecutor(max_workers=len(top) or 1) as pool:
            pwds = list(pool.map(self._fetch_password, [it["info_path"] for it in top]))
        for it, pw in zip(top, pwds):
            it["password"] = pw

        results = []
        for it in items[:self.max_items]:
            results.append({
                "url": _BASE + it["info_path"],
                "password": it.get("password", ""),
                "title": it["title"],
                "disk_type": "baidu",
                "source": "xuebapan",
                "datetime": it.get("date", ""),
            })
        return results

    # ---------- 内部 ----------

    def _search_list(self, kw: str) -> list:
        """抓列表页，解析出有效条目（标题 + /info/ 路径 + 日期）"""
        # 学霸盘的 URL 用 "-" 作分隔符（/s/{词}-{页}.html），
        # 关键词自带破折号会撞坏路由 → 清洗掉；超长书名截断（前缀检索仍有效）
        kw_url = kw.replace("-", "").replace("–", "").strip()
        if len(kw_url) > 30:
            kw_url = kw_url[:30]
        url = f"{_BASE}/s/{requests.utils.quote(kw_url)}-1.html"
        resp = self._session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        body = resp.text

        items = []
        seen = set()
        for m in _ITEM_RE.finditer(body):
            cls, path, raw_title = m.group(1), m.group(2), m.group(3)
            if cls != "valid" or path in seen:
                continue
            seen.add(path)
            # 条目所在局部片段：用于找日期与文件大小
            tail = body[m.end(): m.end() + 1500]
            dm = _DATE_RE.search(tail)
            date = dm.group(0) if dm else ""
            sizes = [float(v) * (1024 if u.upper() == "GB" else 1)
                     for v, u in _SIZE_RE.findall(tail)]
            total_mb = sum(sizes)
            title = _clean_title(raw_title)
            if not title:
                continue
            # 刷量条目过滤：无文件大小信息或总量为 0
            if sizes and total_mb <= 0.01:
                continue
            items.append({"title": title, "info_path": path,
                          "date": date, "total_mb": total_mb})

        # 相关度粗排：标题完全含关键词的靠前；组内按日期新到旧（稳定双排序）
        kw_low = kw.lower().replace(" ", "")
        items.sort(key=lambda it: it["date"], reverse=True)
        items.sort(key=lambda it: 0 if kw_low in it["title"].lower().replace(" ", "") else 1)
        return items[:self.max_items]

    def _fetch_password(self, info_path: str) -> str:
        """抓详情页明文提取码；失败返回空串"""
        try:
            resp = self._session.get(_BASE + info_path, timeout=self.timeout)
            m = _PWD_RE.search(resp.text)
            return m.group(1).strip() if m else ""
        except Exception:
            return ""


# 模块级单例（api/harvester 直接复用）
xuebapan_client = XuebapanClient()
