"""百度网盘分享解析客户端（wxlist 直链协议，不转存、不落库直链）。

协议对齐现役 alist baidu_share 驱动（接口变动只改本文件）：
  1. 链接归一化   normalize_share_text() —— 提取 surl 与提取码 pwd
  2. 验证+列表    share/wxlist (clienttype=25, form 体)
                  form {pwd, root:1, shorturl} → data{seckey, shareid, uk, list}
                  子目录 form {dir, pwd, root:0, shorturl, ...}，has_more 翻页
  3. sign/timestamp  share/tplconfig?fields=sign,timestamp&surl=...（下载时现取）
  4. 换取直链     api/sharedownload?clienttype=12&sign=&timestamp=
                  form {encrypt:0, extra:{sekey}, fid_list, primaryid, product:share,
                        type:nolimit, uk} → list[].dlink（取出立即交给 aria2）
  全程 User-Agent: netdisk（官方客户端 UA，服务端信任度更高，直链下载也用它）。

安全约定：BDUSS/seckey 是敏感凭证，本模块一律不写日志、不放进异常文本。
"""
import json
import logging
import re

import requests

logger = logging.getLogger("accel")

PAN = "https://pan.baidu.com"
APP_ID = "250528"
UA = "netdisk;"  # 官方客户端 UA 形态
TIMEOUT = 20

# share 链接：/s/1AbCdEfG（短码以 1 开头）
_RE_SHARE = re.compile(r"pan\.baidu\.com/s/1([0-9A-Za-z_-]+)")
_RE_PWD_URL = re.compile(r"[?&]pwd=([0-9A-Za-z]{4})")
_RE_PWD_TEXT = re.compile(r"提取码[:：\s]*([0-9A-Za-z]{4})")
_RE_BDUSS = re.compile(r"BDUSS[=:]\s*([0-9A-Za-z]{20,})")
_RE_STOKEN = re.compile(r"STOKEN[=:]\s*([0-9A-Za-z_\-]{10,})")

# 常见 errno → (用户提示, 错误类别)
_ERRNO_MAP = {
    -6: ("BDUSS 已失效，请重新获取并粘贴", "auth"),
    -7: ("该分享已删除或已取消", "expired"),
    -9: ("分享链接已失效（文件被取消或删除）", "expired"),
    -12: ("提取码错误", "pwd"),
    -19: ("需要输入验证码，请稍后再试", "risk"),
    -62: ("触发百度风控验证，请稍后再试", "risk"),
    -70: ("触发百度风控验证，请稍后再试", "risk"),
    -105: ("请求过于频繁，请稍后再试", "risk"),
    105: ("请求被百度风控拦截，请稍后再试（持续出现可重新扫码登录）", "risk"),
    132: ("账号需安全验证，请登录 pan.baidu.com 完成验证后重试", "risk"),
    8001: ("触发百度验证，请稍后再试", "risk"),
    2: ("参数错误，百度接口可能已变动", "parse"),
    9019: ("分享链接不存在或已失效", "expired"),
}


class BaiduError(Exception):
    """解析/下载失败。msg 可直接展示给用户，kind 用于前端着色。"""

    def __init__(self, msg, errno=None, kind="parse"):
        super().__init__(msg)
        self.msg = msg
        self.errno = errno
        self.kind = kind


def _map_errno(errno, fallback="解析失败"):
    for code, (msg, kind) in _ERRNO_MAP.items():
        if errno == code:
            return BaiduError(msg, errno=errno, kind=kind)
    return BaiduError(f"{fallback}（errno={errno}），百度接口可能已变动", errno=errno)


def _s(value) -> str:
    """百度接口同一字段时而数字时而字符串，统一转字符串。"""
    if value is None:
        return ""
    return str(value)


def normalize_share_text(text: str) -> dict:
    """从用户粘贴的文本里提取 surl（含前导 1）与提取码。"""
    text = (text or "").strip()
    m = _RE_SHARE.search(text)
    if not m:
        raise BaiduError("未识别到有效的百度网盘分享链接（形如 pan.baidu.com/s/1xxxx）")
    surl = "1" + m.group(1)
    pwd = ""
    m = _RE_PWD_URL.search(text) or _RE_PWD_TEXT.search(text)
    if m:
        pwd = m.group(1)
    return {"surl": surl, "pwd": pwd}


def normalize_bduss(raw: str) -> str:
    """兼容直接粘贴 BDUSS 值或整段 Cookie 文本（只取 BDUSS）。"""
    raw = (raw or "").strip().strip(';，, "\'')
    m = _RE_BDUSS.search(raw)
    return m.group(1) if m else raw


def extract_credentials(raw: str) -> dict:
    """从粘贴内容提取凭证：支持纯 BDUSS 值、BDUSS=xxx、或整段 Cookie 串。"""
    raw = (raw or "").strip()
    creds = {"BDUSS": "", "STOKEN": ""}
    m = _RE_BDUSS.search(raw)
    if m:
        creds["BDUSS"] = m.group(1)
    m = _RE_STOKEN.search(raw)
    if m:
        creds["STOKEN"] = m.group(1)
    if not creds["BDUSS"]:
        creds["BDUSS"] = raw.strip().strip(';，, "\'')
    return creds


class BaiduShareClient:
    """一个实例绑定一个 BDUSS（用户的登录态）。Cookie 全部走 session jar。"""

    def __init__(self, bduss: str, stoken: str = ""):
        self.bduss = (bduss or "").strip()
        self._sess = requests.Session()
        self._sess.headers.update({
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
        })
        self._sess.cookies.set("BDUSS", self.bduss, domain=".baidu.com", path="/")

    # ---------- 底层请求 ----------
    def _post(self, path, params=None, data=None, refer=None):
        headers = {"Referer": refer} if refer else {}
        return self._sess.post(PAN + path, params=params, data=data,
                               headers=headers, timeout=TIMEOUT)

    def _get(self, path, params=None, refer=None):
        headers = {"Referer": refer} if refer else {}
        return self._sess.get(PAN + path, params=params, headers=headers,
                              timeout=TIMEOUT)

    # ---------- 0. 登录态校验 ----------
    def check_login(self) -> dict:
        """BDUSS 有效性：quota 优先；异常时回退 membership/user/info。"""
        endpoints = (
            ("/api/quota", {"checkfree": 1, "checkexpire": 1,
                            "clienttype": 0, "web": 1}),
            ("/rest/2.0/membership/user/info",
             {"method": "query", "clienttype": 0, "app_id": 250528, "web": 1}),
        )
        last_errno = None
        last_body = ""
        net_err = None
        for path, params in endpoints:
            try:
                r = self._get(path, params=params)
            except requests.RequestException as e:
                net_err = e
                continue
            try:
                j = r.json()
            except ValueError:
                last_body = r.text[:150]
                continue
            if not isinstance(j, dict):
                last_body = str(j)[:150]
                continue
            if j.get("errno") == 0 or j.get("error_code") == 0 \
                    or (isinstance(j.get("user_info"), dict)):
                return {"ok": True, "total": j.get("total"), "used": j.get("used")}
            if j.get("errno") is not None:
                last_errno = j.get("errno")
            else:
                last_body = str(j)[:150]
        if last_errno is not None:
            raise _map_errno(last_errno, fallback="BDUSS 校验失败")
        if net_err is not None and not last_body:
            raise BaiduError(f"网络请求失败：{net_err.__class__.__name__}",
                             kind="network")
        raise BaiduError(f"校验响应异常：{last_body or '空响应'}", kind="parse")

    # ---------- 1. wxlist：验证提取码 + 列目录（一步完成） ----------
    def _wxlist(self, surl: str, pwd: str, root: bool, dir_path: str = "",
                page: int = 1, num: int = 100) -> dict:
        """POST share/wxlist。root=True 列分享根目录；否则列 dir_path 子目录。

        成功返回 data（含 seckey/shareid/uk/list/has_more）。
        """
        form = {
            "pwd": pwd or "",
            "root": "1" if root else "0",
            "shorturl": surl,  # 含前导 1（实测必须带，去掉会 errno=2）
            "num": str(num),
            "order": "time",
            "page": str(page),
        }
        if not root:
            form["dir"] = dir_path
        r = self._post("/share/wxlist",
                       params={"channel": "weixin", "version": "2.2.2",
                               "clienttype": 25, "web": 1},
                       data=form, refer=f"{PAN}/s/{surl}")
        try:
            j = r.json()
        except ValueError:
            raise BaiduError("wxlist 响应异常（非 JSON），接口可能已变动", kind="parse")
        if j.get("errno") != 0:
            raise _map_errno(j.get("errno"), fallback="分享目录获取失败")
        return j.get("data") or {}

    # ---------- 2. 分享元数据 + 根目录 ----------
    def resolve(self, surl: str, pwd: str = "") -> dict:
        """返回 ctx = {surl, pwd, shareid, uk, seckey, root_files}。

        不再需要 sign/timestamp/bdstoken——下载时经 share/tplconfig 现取。
        """
        if not pwd:
            # wxlist 需要提取码字段；无码分享传空串即可
            data = self._wxlist(surl, "", root=True)
        else:
            try:
                data = self._wxlist(surl, pwd, root=True)
            except BaiduError as e:
                if e.kind == "pwd":
                    raise
                # 个别无码分享带空 pwd 失败时，重试一次带 pwd 形式已覆盖；原样抛出
                raise

        root_files = data.get("list") or []
        ctx = {
            "surl": surl, "pwd": pwd or "",
            "shareid": _s(data.get("shareid")),
            "uk": _s(data.get("uk")),
            "seckey": _s(data.get("seckey")),
            "root_files": [_norm_file(f) for f in root_files],
        }
        missing = [k for k in ("shareid", "uk") if not ctx[k]]
        if missing:
            raise BaiduError(
                f"解析失败：未能取得 {','.join(missing)}，百度接口可能已变动，"
                f"请到项目页反馈", kind="parse")
        return ctx

    # ---------- 3. 目录列表 ----------
    def list_dir(self, ctx: dict, dir_path: str = "/") -> list:
        """列出子目录（dir_path 为分享内绝对路径）。"""
        data = self._wxlist(ctx["surl"], ctx.get("pwd", ""), root=False,
                            dir_path=dir_path)
        return [_norm_file(f) for f in (data.get("list") or [])]

    # ---------- 4. sign/timestamp（下载时现取） ----------
    def _fetch_sign(self, surl: str) -> tuple:
        r = self._get("/share/tplconfig",
                      params={"fields": "sign,timestamp", "channel": "chunlei",
                              "web": 1, "app_id": 250528, "clienttype": 0,
                              "surl": surl},
                      refer=f"{PAN}/s/{surl}")
        try:
            j = r.json()
        except ValueError:
            raise BaiduError("tplconfig 响应异常，接口可能已变动", kind="parse")
        if j.get("errno") != 0:
            raise _map_errno(j.get("errno"), fallback="sign 获取失败")
        d = j.get("data") or {}
        return _s(d.get("sign")), _s(d.get("timestamp"))

    # ---------- 5. 换直链（不缓存，取出立即用） ----------
    def fetch_dlinks(self, ctx: dict, fids: list) -> list:
        """POST api/sharedownload → [{fs_id, dlink, filename, size}]。

        dlink 有时效且绑定账号，调用方必须立刻推给 aria2，绝不落库/缓存。
        """
        seckey = ctx.get("seckey", "")
        if not seckey:
            # ctx 过期（如 403 重取）时先重建 seckey
            data = self._wxlist(ctx["surl"], ctx.get("pwd", ""), root=True)
            ctx["seckey"] = seckey = _s(data.get("seckey"))
            ctx["shareid"] = ctx["shareid"] or _s(data.get("shareid"))
            ctx["uk"] = ctx["uk"] or _s(data.get("uk"))

        sign, timestamp = self._fetch_sign(ctx["surl"])
        r = self._post("/api/sharedownload",
                       params={"app_id": 250528, "channel": "chunlei",
                               "clienttype": 0, "web": 1,
                               "sign": sign, "timestamp": timestamp},
                       data={
                           "encrypt": "0",
                           "extra": json.dumps({"sekey": seckey}),
                           "fid_list": json.dumps([int(f) for f in fids]),
                           "primaryid": str(ctx["shareid"]),
                           "product": "share",
                           "type": "nolimit",
                           "uk": str(ctx["uk"]),
                       },
                       refer=f"{PAN}/s/{ctx['surl']}")
        try:
            j = r.json()
        except ValueError:
            raise BaiduError("直链响应异常（非 JSON），接口可能已变动", kind="parse")
        if j.get("errno") != 0:
            err = _map_errno(j.get("errno"), fallback="直链获取失败")
            extra = j.get("show_msg") or j.get("errmsg") or ""
            if extra and extra not in err.msg:
                # 百度原始提示（如"文件过大请使用客户端"）必须透出给用户
                err = BaiduError(f"{err.msg}（百度提示：{extra}）",
                                 errno=err.errno, kind=err.kind)
            raise err
        out = []
        for it in (j.get("list") or []):
            if it.get("dlink"):
                out.append({
                    "fs_id": _s(it.get("fs_id")),
                    "dlink": it["dlink"],
                    "filename": it.get("server_filename", ""),
                    "size": int(it.get("size") or 0),
                })
        if not out:
            raise BaiduError("直链获取失败：接口返回为空，可能该分享禁止下载")
        return out

    # ---------- 下载用 cookie 串（交给 aria2 header） ----------
    def cookie_header(self) -> str:
        return f"BDUSS={self.bduss}"


def _norm_file(it: dict) -> dict:
    """百度各接口文件字段名不一（fs_id/fsid、server_filename、isdir…），归一化。"""
    path = it.get("path") or it.get("server_filename") or ""
    name = it.get("server_filename") or path.rstrip("/").rsplit("/", 1)[-1]
    return {
        "fid": _s(it.get("fs_id") or it.get("fsid") or ""),
        "path": path,
        "name": name,
        "size": int(it.get("size") or 0),
        "is_dir": bool(it.get("isdir") or it.get("is_dir") or it.get("dir")),
    }
