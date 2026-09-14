"""百度扫码登录（passport 二维码），替代手动复制 BDUSS。

流程（端点经 BaiduPCS-Rust 等现役实现验证；接口变动只改本文件）：
  1. /v2/api/getqrcode?tpl=netdisk      → sign（会话需保留其下发的 BAIDUID）
  2. /v2/api/qrcode?sign=..&lp=mobile   → 二维码图片（lp=mobile 供手机 App 扫）
  3. /channel/unicast?channel_id=sign   → 轮询，channel_v(JSON 字符串) 内含
     status：0=等待 / 1=已扫码待确认 / 有 v=已确认 / -1,-2=已过期
  4. /v3/login/main/qrbdusslogin?bduss={v} → Set-Cookie 下发 BDUSS/STOKEN
  5. check_login 校验后由路由层入库

安全约定：BDUSS/STOKEN 不写日志、不进异常文本。
"""
import json
import logging
import re
import time
import uuid

import requests

from .baidu import UA

logger = logging.getLogger("accel")

PASSPORT = "https://passport.baidu.com"
POLL_MIN_INTERVAL = 1.5   # 两次轮询百度之间的最小间隔（秒）
QR_TTL = 180              # 二维码本地有效期（秒），服务端 -1/-2 为准


class QRLoginError(Exception):
    def __init__(self, msg):
        super().__init__(msg)
        self.msg = msg


def _unwrap_jsonp(text: str) -> dict:
    """passport 接口返回 JSONP（callback({...})）或纯 JSON，统一解包。"""
    m = re.search(r"\((\{.*\})\)", text.strip(), re.S)
    raw = m.group(1) if m else text
    return json.loads(raw)


def _now_ms() -> int:
    return int(time.time() * 1000)


class QRLoginSession:
    """一次扫码登录会话（同一时刻只保留一个活跃会话）。"""

    def __init__(self):
        self.sign = ""
        self.imgurl = ""
        self.status = "waiting"   # waiting|scanned|confirmed|expired|error
        self.error = ""
        self.created_at = time.time()
        self._last_poll = 0.0
        self._sess = requests.Session()   # cookie jar 保留 getqrcode 下发的 BAIDUID
        self._sess.headers.update({"User-Agent": UA})
        self.bduss = ""
        self.stoken = ""

    # ---- 步骤 1+2：生成二维码 ----
    def create(self):
        try:
            r = self._sess.get(
                PASSPORT + "/v2/api/getqrcode",
                params={"lp": "pc", "qrloginfrom": "pc", "gid": str(uuid.uuid4()).upper(),
                        "callback": "qrCb", "apiver": "v3", "tt": _now_ms(),
                        "tpl": "netdisk"},
                timeout=15)
            data = _unwrap_jsonp(r.text)
        except Exception as e:
            raise QRLoginError(f"获取二维码失败：{e.__class__.__name__}")
        if data.get("errno") != 0:
            raise QRLoginError(
                f"获取二维码失败（errno={data.get('errno')}），接口可能已变动")
        sign = str(data.get("sign") or "")
        if not sign:
            imgurl_raw = str(data.get("imgurl") or "")
            m = re.search(r"sign=([0-9a-f]+)", imgurl_raw)
            sign = m.group(1) if m else ""
        if not sign:
            raise QRLoginError("二维码响应缺少 sign，接口可能已变动")
        self.sign = sign
        # lp=mobile 使二维码内容为手机 App 可扫的登录确认页
        self.imgurl = (f"{PASSPORT}/v2/api/qrcode?sign={sign}"
                       f"&lp=mobile&qrloginfrom=mobile&tpl=netdisk")
        self.status = "waiting"
        self.created_at = time.time()

    # ---- 步骤 3：轮询状态（内部限频，单次网络失败不置错误） ----
    def poll(self) -> str:
        if self.status in ("confirmed", "expired", "error"):
            return self.status
        if time.time() - self.created_at > QR_TTL:
            self.status = "expired"
            return self.status
        if time.time() - self._last_poll < POLL_MIN_INTERVAL:
            return self.status
        self._last_poll = time.time()
        try:
            r = self._sess.get(
                PASSPORT + "/channel/unicast",
                params={"channel_id": self.sign, "tpl": "netdisk",
                        "apiver": "v3", "tt": _now_ms()},
                timeout=15)
            data = r.json()
        except Exception as e:
            logger.warning("[加速] 扫码轮询单次失败: %s", e.__class__.__name__)
            return self.status

        # channel_v 是一段 JSON 字符串：{"status": N, "v": "..."}
        cv_raw = data.get("channel_v") or "{}"
        try:
            cv = json.loads(cv_raw) if isinstance(cv_raw, str) else cv_raw
        except ValueError:
            cv = {}
        status = cv.get("status")
        v_code = str(cv.get("v") or "")

        if v_code:
            self._confirm(v_code)
        elif status == 1:
            self.status = "scanned"
        elif status == 0:
            self.status = "waiting"
        elif status in (-1, -2):
            self.status = "expired"
            self.error = "二维码已过期，请刷新"
        elif status == 2:
            self.status = "expired"
            self.error = "登录状态异常，请刷新二维码重试"
        elif data.get("errno") not in (None, 0):
            self.status = "expired"
            self.error = f"轮询异常（errno={data.get('errno')}），请刷新重试"
        else:
            self.status = "waiting"
        return self.status

    # ---- 步骤 4：确认后换完整 cookie ----
    def _confirm(self, v_code: str):
        try:
            # 跟随重定向：STOKEN 等可能在后续跳转的 Set-Cookie 里下发
            self._sess.get(
                PASSPORT + "/v3/login/main/qrbdusslogin",
                params={"v": _now_ms(), "bduss": v_code,
                        "u": "https://pan.baidu.com/disk/main",
                        "tpl": "netdisk", "qrcode": "1", "apiver": "v3",
                        "tt": _now_ms()},
                timeout=15)
        except Exception as e:
            logger.warning("[加速] qrbdusslogin 请求失败: %s", e.__class__.__name__)
        jar = self._sess.cookies
        self.bduss = jar.get("BDUSS", domain=".baidu.com") or jar.get("BDUSS") or ""
        self.stoken = jar.get("STOKEN", domain=".baidu.com") or jar.get("STOKEN") or ""
        if self.bduss:
            self.status = "confirmed"
            logger.info("[加速] 扫码换得 Cookie: %s",
                        sorted({c.name for c in jar}))
            logger.info("[加速] 扫码登录成功（尾号 %s）", self.bduss[-4:])
        else:
            self.status = "error"
            self.error = "扫码确认成功但未能取得 BDUSS，接口可能已变动"
            logger.warning("[加速] %s（jar 内 cookie: %s）", self.error,
                           sorted({c.name for c in jar}))

    @property
    def cookie_jar(self):
        """完整 cookie jar（含 BAIDUID 等预热字段），供校验时合并使用。"""
        return self._sess.cookies
