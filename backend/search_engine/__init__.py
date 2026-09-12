"""
search_engine —— PanSou 搜索引擎适配层

你的 Python 后端不直接写搜索逻辑，而是通过 HTTP 调用本地的
PanSou Go 服务（http://localhost:8888）来执行实时搜索。

这样做的好处：
  1. 你不需要看懂 Go 代码
  2. 你的项目架构更干净 —— PanSou 是"搜索引擎基础设施"，你是"上层应用"
  3. 可以随时更换搜索引擎后端（比如换个自建的 ES 集群）

PanSou API 文档：
  POST /api/search
    {"kw": "关键词", "res": "merge", "src": "all"}
"""
import requests
import json
import time
import hashlib
import logging
from typing import Optional
from config import Config

logger = logging.getLogger("search_engine")


class PansouClient:
    """PanSou 搜索引擎的 HTTP 客户端"""

    def __init__(self, base_url: str = None, timeout: int = None,
                 auth_username: str = None, auth_password: str = None):
        self.base_url = base_url or Config.PANSOU_BASE_URL
        self.timeout = timeout or Config.PANSOU_TIMEOUT
        self._cache = {}  # 简单内存缓存
        # 认证支持：若 PanSou 开启了 AUTH_ENABLED，自动登录获取 token
        self.username = auth_username or Config.PANSOU_USERNAME
        self.password = auth_password or Config.PANSOU_PASSWORD
        self._token = None
        self._token_expiry = 0

    # ---------- 认证 ----------
    def _ensure_token(self) -> str:
        """确保有有效 token；PanSou 关闭认证时返回空串"""
        if self._token and time.time() < self._token_expiry:
            return self._token
        if not self.username or not self.password:
            return ""
        try:
            resp = requests.post(
                f"{self.base_url}/api/auth/login",
                json={"username": self.username, "password": self.password},
                timeout=8,
            )
            if resp.status_code == 200:
                data = resp.json()
                self._token = data.get("token", "")
                exp = data.get("expires_at", 0)
                # 提前 60 秒过期，避免临界失效
                self._token_expiry = exp - 60 if exp else time.time() + 60 * 60
                return self._token
        except Exception as e:
            logger.warning(f"PanSou 登录失败: {e}")
        return ""

    def _auth_headers(self) -> dict:
        token = self._ensure_token()
        if token:
            return {"Authorization": f"Bearer {token}"}
        return {}

    def search(self, keyword: str, src: str = "all",
               plugins: list = None, cloud_types: list = None,
               force_refresh: bool = False, result_type: str = "merge") -> dict:
        """
        调用 PanSou 进行实时搜索

        参数:
            keyword: 搜索关键词
            src: 数据来源（all/tg/plugin）
            plugins: 指定插件列表（None 表示所有）
            cloud_types: 网盘类型过滤
            force_refresh: 强制刷新（跳过 PanSou 缓存）
            result_type: 返回结果格式（merge/all/results）

        返回:
            PanSou 的原始 JSON 响应（dict），包含 results 和 merged_by_type
        """
        url = f"{self.base_url}/api/search"

        payload = {
            "kw": keyword,
            "res": result_type,
            "src": src,
        }
        if plugins:
            payload["plugins"] = plugins
        if cloud_types:
            payload["cloud_types"] = cloud_types
        if force_refresh:
            payload["refresh"] = True

        headers = {"Content-Type": "application/json"}

        def attempt(auth_headers):
            req_headers = {**headers, **auth_headers}
            resp = requests.post(url, json=payload, timeout=self.timeout, headers=req_headers)
            if resp.status_code == 401:
                # 认证失败，尝试重新登录一次
                self._token = None
                return None
            resp.raise_for_status()
            return resp.json()

        try:
            result = attempt(self._auth_headers())
            if result is None:
                # 刷新 token 后重试一次
                self._token = None
                self._token_expiry = 0
                result = attempt(self._auth_headers())
            if result is None:
                return {
                    "error": True,
                    "message": f"无法通过 PanSou 认证 ({self.base_url})，请检查 PANSOU_USERNAME/PANSOU_PASSWORD 配置"
                }
            return result
        except requests.exceptions.ConnectionError:
            return {
                "error": True,
                "message": f"无法连接到 PanSou 服务 ({self.base_url})，请确认 PanSou 已启动"
            }
        except requests.exceptions.Timeout:
            return {
                "error": True,
                "message": f"PanSou 搜索超时（{self.timeout}s）"
            }
        except requests.exceptions.RequestException as e:
            return {
                "error": True,
                "message": f"PanSou 请求失败: {str(e)}"
            }

    def check_health(self) -> dict:
        """检查 PanSou 是否在线"""
        url = f"{self.base_url}/api/health"
        try:
            resp = requests.get(url, timeout=5)
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return {"status": "offline"}

    def check_links(self, items: list) -> dict:
        """
        调用 PanSou 的链接存活检测

        参数:
            items: [{"disk_type": "quark", "url": "...", "password": ""}]

        返回:
            检测结果列表
        """
        url = f"{self.base_url}/api/check/links"

        def attempt(auth_headers):
            resp = requests.post(
                url,
                json={"items": items},
                timeout=30,
                headers={**auth_headers, "Content-Type": "application/json"},
            )
            if resp.status_code == 401:
                self._token = None
                return None
            resp.raise_for_status()
            return resp.json()

        try:
            result = attempt(self._auth_headers())
            if result is None:
                self._token = None
                self._token_expiry = 0
                result = attempt(self._auth_headers())
            if result is None:
                return {"error": "认证失败", "results": []}
            return result
        except Exception as e:
            return {"error": str(e), "results": []}


def extract_links_from_result(response: dict) -> list:
    """
    从 PanSou 的搜索结果中提取扁平化链接列表

    优先使用 res=all 的原始消息格式（链接更多、标题更干净）:
    {
        "data": {
            "results": [
                {
                    "title": "消息标题", "content": "...",
                    "channel": "", "unique_id": "xiaokupan-xxx",
                    "datetime": "...",
                    "links": [{"type": "quark", "url": "...",
                               "password": "...", "work_title": "..."}]
                }, ...
            ]
        }
    }

    兼容 res=merge 的聚合格式:
    {"data": {"merged_by_type": {"baidu": [{"url":...,"note":...}]}}}
    """
    links = []

    # 处理包装的响应格式
    data = response.get("data") or response

    # ---- 原始消息格式（res=all）：一条消息可含多个网盘链接 ----
    raw_results = data.get("results") if isinstance(data, dict) else None
    if raw_results:
        for msg in raw_results:
            if not isinstance(msg, dict):
                continue
            title = (msg.get("title") or msg.get("content") or "").strip()
            uid = str(msg.get("unique_id") or "")
            source = (msg.get("channel") or uid.split("-")[0] or "unknown").strip()
            msg_dt = msg.get("datetime", "")
            for l in (msg.get("links") or []):
                url = (l.get("url") or "").strip()
                if not url:
                    continue
                links.append({
                    "url": url,
                    "password": (l.get("password") or "").strip(),
                    "title": (l.get("work_title") or title),
                    "disk_type": (l.get("type") or "").strip().lower(),
                    "source": source,
                    "datetime": l.get("datetime") or msg_dt,
                })
        if links:
            return links

    # ---- merge 聚合格式（兼容回退）----
    merged = data.get("merged_by_type") or {}
    for disk_type, items in merged.items():
        for item in items:
            links.append({
                "url": item.get("url", ""),
                "password": item.get("password", ""),
                "title": item.get("note", item.get("title", "")),
                "disk_type": disk_type,
                "source": item.get("source", "unknown"),
                "datetime": item.get("datetime", ""),
            })

    return links