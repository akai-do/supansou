"""跨域（CORS）支持：让**云端页面**能驱动**本机后端**。

## 为什么需要
「下载提速」是把 hook DLL 注入到**与后端同机**的百度网盘客户端进程里，所以它只能由
运行在你 Windows 上的那个后端来做。但你可能是在 `https://xiaowusu.com/booster`
这个页面上操作 —— 那个页面来自云端，于是浏览器要去请求 `http://127.0.0.1:5000`
（你自己的机器）。这属于跨域请求，需要两个响应头：

1. `Access-Control-Allow-Origin`：只放行白名单来源（环境变量 `ACCEL_CORS_ORIGINS`）；
2. `Access-Control-Allow-Private-Network`：Chrome 的私有网络访问（PNA）规定，
   **公网页面访问 loopback/内网地址会先发一个预检并要求响应带这个头**，
   否则浏览器直接拦掉、连请求都不发（表现为"CORS 错误"或直接失败）。

## 安全
只放行白名单来源，且 accel 的鉴权仍然生效（站长令牌/回环判定）——
**放行跨域 ≠ 放开权限**：从云端页面发来的请求若不带正确令牌，照样 403。
预检（OPTIONS）必须放行且不要求鉴权，这是 CORS 规范要求。
"""
import os

from flask import make_response, request

# 默认只放行线上站点；要加就在 ACCEL_CORS_ORIGINS 里逗号分隔（可用 * 表示放行全部，不建议）
DEFAULT_ORIGINS = "https://xiaowusu.com,https://www.xiaowusu.com"


def allowed_origins() -> list:
    raw = os.getenv("ACCEL_CORS_ORIGINS", DEFAULT_ORIGINS)
    return [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]


def _origin_ok(origin: str) -> bool:
    if not origin:
        return False
    o = origin.rstrip("/")
    allow = allowed_origins()
    return "*" in allow or o in allow


def register_cors(app):
    """挂到 Flask app 上。app 级 before_request 会先于蓝图守卫执行，
    所以 OPTIONS 预检能被短路放行（预检不带令牌，不能要求鉴权）。"""

    @app.before_request
    def _cors_preflight():
        if request.method == "OPTIONS" and _origin_ok(request.headers.get("Origin", "")):
            return make_response("", 204)
        return None

    @app.after_request
    def _cors_headers(resp):
        origin = request.headers.get("Origin", "")
        if _origin_ok(origin):
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Vary"] = "Origin"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = request.headers.get(
                "Access-Control-Request-Headers",
                "Content-Type, X-Access-Token, X-Parse-Pass")
            resp.headers["Access-Control-Max-Age"] = "600"
            # Chrome PNA：公网页面访问 127.0.0.1 必须带这个头才放行
            if request.headers.get("Access-Control-Request-Private-Network") == "true":
                resp.headers["Access-Control-Allow-Private-Network"] = "true"
        return resp
