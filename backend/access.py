"""站长身份判定（accel 蓝图与主 API 共用一份实现，避免两处规则漂移）。

规则（按顺序）：
  1. 来源是回环地址（`127.0.0.1` / `::1`）→ **站长**（本机部署时免配置）；
  2. 请求头 `X-Access-Token` 与环境变量 `ACCEL_TOKEN` 一致 → **站长**；
  3. 其余 → **访客**。

⚠ 反向代理部署必须配合 `ProxyFix`（见 `app.py` 的 `TRUST_PROXY`）：
  否则 nginx 反代后 `remote_addr` 恒为 `127.0.0.1`，**所有公网访客都会被判成站长**，
  解析口令与配额会被整体绕过。

令牌比较用 `hmac.compare_digest`（常数时间），避免逐字节比较泄露令牌。
"""
import hmac
import ipaddress
import os

TOKEN_HEADER = "X-Access-Token"
LOOPBACK = ("127.0.0.1", "::1")


def token_ok(supplied: str, expected: str) -> bool:
    if not expected or not supplied:
        return False
    return hmac.compare_digest(supplied, expected)


def _is_local_peer(addr: str) -> bool:
    """直连方是否来自我们自己的基础设施（回环或内网/容器网段）。

    只有这种情况才值得相信 `X-Forwarded-For` 还原出来的客户端 IP：
    nginx 反代时直连方是 127.0.0.1；docker 端口映射时是容器网桥网关（如 172.21.0.1）。
    """
    if not addr:
        return False
    if addr in LOOPBACK:
        return True
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        try:
            ip = ipaddress.ip_address(ip.ipv4_mapped)
        except ValueError:
            return False
    return bool(ip.is_loopback or ip.is_private or ip.is_link_local)


def raw_peer(req) -> str:
    """ProxyFix 改写**之前**的直连方地址。"""
    return req.environ.get("dps.raw_peer") or (req.environ.get("REMOTE_ADDR") or "")


def is_station(req) -> bool:
    """这个请求是否应被视为站长。"""
    # 直连方不是内网 → 有人绕过反代直连后端，此时**不信**任何转发头，
    # 否则伪造 `X-Forwarded-For: 127.0.0.1` 就能冒充站长。
    if not _is_local_peer(raw_peer(req)):
        return token_ok(req.headers.get(TOKEN_HEADER, ""), os.getenv("ACCEL_TOKEN", ""))
    if (req.remote_addr or "") in LOOPBACK:
        return True
    return token_ok(req.headers.get(TOKEN_HEADER, ""), os.getenv("ACCEL_TOKEN", ""))


def token_required() -> bool:
    """是否配置了站长令牌（前端据此提示"远程部署需填令牌"）。"""
    return bool(os.getenv("ACCEL_TOKEN", ""))
