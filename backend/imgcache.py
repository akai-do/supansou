"""通用图片抓取 + 磁盘缓存（供 `/api/poster/img` 使用）。

## 为什么需要
索引里约 2/3 的记录没有封面图；有图的那部分又大量托管在 Telegram CDN
（`cdn*.telesco.pe`）——**站长本机在墙内直连不通**，于是结果卡长期显示占位块。
本模块由**服务端**（部署在香港）回源抓取并落盘：抓到一次，之后所有人打开都是秒出。

原实现只有 5 个域名的白名单 + 纯内存 LRU（重启即失效），非白名单直接 400。

## 安全（本端点是公网可访问的，SSRF 是硬要求）
* 只允许 `http` / `https`，且 URL 不得内嵌账号密码；
* **每一跳**（含 302/301 重定向）都重新解析域名并校验：解析出的**所有** IP 都必须是
  公网地址（用 `ipaddress.is_global` 判定，天然拒绝 `127.`、`10.`、`172.16-31.`、
  `192.168.`、`169.254.169.254`、`100.64/10`、`::1`、`fe80::` 等）；
* 只接受 `Content-Type: image/*`；单张体积上限；跳转次数上限；连接/读取超时；
* 按 IP 做轻量限流，避免被人当免费图床代理刷带宽。

已知残余风险：校验解析结果与真正建连之间存在极小的 DNS 重绑定窗口（TOCTOU）。
要彻底消除需要把连接固定到已校验的 IP，会牺牲 HTTPS 证书校验，故此处不采用。
"""
import hashlib
import ipaddress
import os
import socket
import threading
import time
from urllib.parse import urlparse, urljoin

import requests

# ---------------- 配置 ----------------
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CACHE_DIR = os.environ.get("IMG_CACHE_DIR") or os.path.join(DATA_DIR, "imgcache")
CACHE_MAX_MB = int(os.environ.get("IMG_CACHE_MAX_MB", "512"))     # 磁盘缓存总量上限
MAX_BYTES = int(os.environ.get("IMG_MAX_MB", "5")) * 1024 * 1024  # 单张上限
MAX_REDIRECTS = 3
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 12
RATE_LIMIT_PER_MIN = int(os.environ.get("IMG_RATE_PER_MIN", "120"))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_CT_EXT = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
    "image/webp": ".webp", "image/gif": ".gif", "image/avif": ".avif",
    "image/svg+xml": ".svg", "image/bmp": ".bmp", "image/x-icon": ".ico",
}

# 部分图床校验 Referer 才给图（Telegram CDN 偶发 403）。豆瓣走 DoubanClient 那条路，
# 这里主要是给 TG 系图床补一个体面的来源。
_REFERER = {
    "telesco.pe": "https://t.me/",
    "cdn-telegram.org": "https://t.me/",
    "telegram.org": "https://t.me/",
    "t.me": "https://t.me/",
}


def _referer_for(host: str) -> str:
    for suf, ref in _REFERER.items():
        if host == suf or host.endswith("." + suf):
            return ref
    return ""


class UnsafeURL(ValueError):
    """URL 未通过安全校验（应当直接拒绝，不要当成"拉取失败"）。"""


# ---------------- SSRF 校验 ----------------
def _ip_ok(ip_str: str) -> bool:
    """只放行公网地址（is_global 天然排除私网/回环/链路本地/云元数据等）。"""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        return _ip_ok(str(ip.ipv4_mapped))
    return bool(ip.is_global)


def check_url(url: str) -> str:
    """校验并返回规范化 URL；不通过抛 UnsafeURL。"""
    u = (url or "").strip()
    if not u:
        raise UnsafeURL("缺少图片地址")
    if len(u) > 2000:
        raise UnsafeURL("地址过长")
    p = urlparse(u)
    if p.scheme not in ("http", "https"):
        raise UnsafeURL(f"只允许 http/https（收到 {p.scheme or '空'}）")
    if "@" in (p.netloc or ""):
        raise UnsafeURL("地址不得内嵌账号密码")
    host = p.hostname or ""
    if not host:
        raise UnsafeURL("缺少主机名")
    try:
        infos = socket.getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80),
                                  proto=socket.IPPROTO_TCP)
    except OSError as e:
        raise UnsafeURL(f"无法解析主机名 {host}：{e}") from e
    if not infos:
        raise UnsafeURL(f"无法解析主机名 {host}")
    bad = [sockaddr[0] for *_, sockaddr in infos if not _ip_ok(sockaddr[0])]
    if bad:
        # 只要有一个解析结果是内网地址就整体拒绝（防"公网+内网"混合解析绕过）
        raise UnsafeURL(f"主机名 {host} 解析到非公网地址 {bad[0]}")
    return u


# ---------------- 按 IP 限流 ----------------
class _RateLimiter:
    """极简滑动窗口限流（按 IP，每 RATE_LIMIT_PER_MIN 条/分钟）。"""

    def __init__(self, limit: int, window: float = 60.0):
        self._limit = limit
        self._window = window
        self._hits = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            arr = [t for t in self._hits.get(key, []) if now - t < self._window]
            if len(arr) >= self._limit:
                self._hits[key] = arr
                return False
            arr.append(now)
            self._hits[key] = arr
            if len(self._hits) > 5000:      # 防无限增长
                self._hits = {k: v for k, v in self._hits.items() if v}
            return True


LIMITER = _RateLimiter(RATE_LIMIT_PER_MIN)


# ---------------- 磁盘缓存 ----------------
def _key(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


def _cache_paths(url: str):
    """返回 (命中路径, 可能存在的内容类型) —— 文件名按扩展名索引，逐个探测。"""
    k = _key(url)
    d = CACHE_DIR
    for ct, ext in _CT_EXT.items():
        p = os.path.join(d, k + ext)
        if os.path.isfile(p):
            return p, ct
    return None, None


def _save(url: str, body: bytes, ct: str):
    os.makedirs(CACHE_DIR, exist_ok=True)
    ext = _CT_EXT.get(ct.split(";")[0].strip().lower(), ".bin")
    final = os.path.join(CACHE_DIR, _key(url) + ext)
    tmp = final + ".tmp"
    with open(tmp, "wb") as f:
        f.write(body)
    os.replace(tmp, final)          # 原子替换，避免读到半个文件
    _evict_if_needed()


_evict_lock = threading.Lock()
_last_evict = [0.0]


def _evict_if_needed():
    """按 mtime 从旧到新淘汰，直到总量回到上限内（最多每 30s 检查一次）。"""
    now = time.time()
    with _evict_lock:
        if now - _last_evict[0] < 30:
            return
        _last_evict[0] = now
        try:
            files = []
            total = 0
            for name in os.listdir(CACHE_DIR):
                p = os.path.join(CACHE_DIR, name)
                try:
                    if name.endswith(".tmp"):
                        os.remove(p)     # 清理中断留下的临时文件
                        continue
                    st = os.stat(p)
                except OSError:
                    continue
                files.append((st.st_mtime, st.st_size, p))
                total += st.st_size
            cap = CACHE_MAX_MB * 1024 * 1024
            if total <= cap:
                return
            files.sort()                  # 最旧优先
            for _mt, size, p in files:
                if total <= cap:
                    break
                try:
                    os.remove(p)
                    total -= size
                except OSError:
                    pass
        except OSError:
            pass


def cache_stat() -> dict:
    """缓存现状（给排查用）。"""
    n = total = 0
    try:
        for name in os.listdir(CACHE_DIR):
            p = os.path.join(CACHE_DIR, name)
            try:
                total += os.stat(p).st_size
                n += 1
            except OSError:
                continue
    except OSError:
        pass
    return {"files": n, "mb": round(total / 1e6, 1), "cap_mb": CACHE_MAX_MB}


# ---------------- 抓取 ----------------
def fetch(url: str, referer: str = "") -> tuple:
    """抓取图片，返回 (bytes, content_type)。命中磁盘缓存直接返回。

    抛出 UnsafeURL（URL 不合法/不安全）或其他异常（网络/内容问题）。
    """
    u = check_url(url)
    hit, ct = _cache_paths(u)
    if hit:
        try:
            with open(hit, "rb") as f:
                return f.read(), ct
        except OSError:
            pass

    headers = {"User-Agent": UA, "Accept": "image/*,*/*;q=0.8"}
    ref = referer or _referer_for(urlparse(u).hostname or "")
    if ref:
        headers["Referer"] = ref

    cur = u
    for _hop in range(MAX_REDIRECTS + 1):
        # 每一跳都重新校验（防止 302 到内网地址）
        cur = check_url(cur)
        r = requests.get(cur, headers=headers, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                         stream=True, allow_redirects=False)
        try:
            if r.status_code in (301, 302, 303, 307, 308):
                loc = r.headers.get("Location")
                if not loc:
                    raise ValueError("重定向缺少 Location")
                cur = urljoin(cur, loc)
                continue
            if r.status_code >= 400:
                raise ValueError(f"上游返回 HTTP {r.status_code}")
            ct_raw = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if not ct_raw.startswith("image/"):
                raise ValueError(f"目标不是图片（Content-Type={ct_raw or '空'}）")
            # 分块读并卡上限，避免被超大响应拖死
            chunks, size = [], 0
            for chunk in r.iter_content(65536):
                if not chunk:
                    continue
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError(f"图片超过 {MAX_BYTES // 1048576}MB 上限")
                chunks.append(chunk)
            body = b"".join(chunks)
            if not body:
                raise ValueError("空响应")
            _save(u, body, ct_raw)
            return body, ct_raw
        finally:
            r.close()
    raise ValueError("重定向次数过多")
