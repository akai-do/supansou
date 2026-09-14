"""网盘加速 REST 端点（url_prefix=/api/accel，由 accel/__init__.py 注册）。

第三版新增提速面：
  GET/PUT /speedup            档位、并发、让路阈值、带宽闸门、分段开关
  GET     /speedup/diagnose   当前速度归因（本地触顶 / 单连接限速 / 账号级限速）
  GET/POST/DELETE /accounts   多账号池
  POST    /tasks/<id>/priority 置顶任务（让路时最后才被牺牲）
  POST    /download 支持多账号分流与分段跨账号下载
"""
import hashlib
import hmac
import logging
import os
import re
import threading
import time

from flask import jsonify, request

import access          # backend/access.py（与主 API 共用同一份站长判定）
from . import tuning
from .baidu import PAN, BaiduError, extract_credentials, normalize_share_text
from .booster import OpenSpeedyHookConflict
from .qrlogin import QRLoginError, QRLoginSession
from .store import mask_secret

# ---------------------------------------------------------------- 解析口令
# 口令文件每行一条：
#     2026-09-13:abc123    只在 2026-09-13 有效（过期自动失效）
#     abc123               永久有效
#     # 开头的行 / 空行      忽略
# 语义：
#   * 文件不存在、或**当前没有有效行** → 不启用口令（退回只按 IP 配额），
#     这样不会把已有部署锁死；想启用就往文件里写。
#   * 每次请求都重读文件 → 追加"今天那一行"立刻生效，不用重启服务。
#   * 文件里可以同时留多行（今天/昨天/朋友A专用），泄露了就只删那一行，
#     不影响其他人；用量按指纹入库，能看出是哪条在被猛用。
# 校验点在服务端；前端弹窗只是 UX，绕过弹窗也过不了。
DEFAULT_PASS_FILE = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "parse_pass.txt"))
PASS_HEADER = "X-Parse-Pass"
PASS_REQUIRED_PATHS = {"/parse", "/list-dir", "/dlinks"}
_PASS_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def pass_state(path: str, today: str = "") -> dict:
    """解析口令文件。

    返回 {'configured': bool, 'valid': [(day|None, pw)], 'lines': int}

    `configured` 区分两件必须分开的事：
      * False = 文件不存在/没有条目 → **不启用口令**（退回只按 IP 配额），
        这样已有部署不会被锁死；
      * True 但 `valid` 为空 = 配了口令、但**今天没有有效的行**（比如忘了追加
        今天那行）→ 调用方应当**一律拒绝**（fail closed）。
        不能退回"不启用"——那等于忘更新就把会员账号的解析额度敞开了。
    """
    today = today or time.strftime("%Y-%m-%d")
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        return {"configured": False, "valid": [], "lines": 0}
    valid, lines = [], 0
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        day, pw = None, s
        if ":" in s:
            head, tail = s.split(":", 1)
            if _PASS_DATE_RE.match(head.strip()):
                day, pw = head.strip(), tail.strip()
        if not pw:
            continue
        lines += 1
        if day is None or day == today:
            valid.append((day, pw))
    return {"configured": lines > 0, "valid": valid, "lines": lines}


def pass_entries(path: str, today: str = "") -> list:
    """当前有效的 [(day|None, password)]（pass_state 的薄封装，便于单测）。"""
    return pass_state(path, today)["valid"]


def pass_fp(pw: str) -> str:
    """口令指纹（sha256 前 12 位）——用量入库时不落明文。"""
    return hashlib.sha256(pw.encode("utf-8")).hexdigest()[:12]


def pass_check(path: str, supplied: str, today: str = ""):
    """(是否通过, 指纹)。未启用口令 → (True, None)；今天无有效口令 → (False, None)。"""
    st = pass_state(path, today)
    if not st["configured"]:
        return True, None
    if not supplied:
        return False, None
    for _day, pw in st["valid"]:
        if hmac.compare_digest(supplied, pw):
            return True, pass_fp(pw)
    return False, None


# 自动生成口令用的字母表：去掉易混淆的 i l o 0 1，方便口头/截图转发
_PASS_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def gen_pass(n: int = 10) -> str:
    import secrets
    return "".join(secrets.choice(_PASS_ALPHABET) for _ in range(n))


def pass_lines(path: str) -> list:
    """返回 [(行号(1 起), 原文)]，只含"条目行"（跳过空行与 # 注释）。

    给"按行吊销"用：站长在页面上删某一条口令时，需要能精确定位到文件里的哪一行。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        return []
    out = []
    for i, line in enumerate(raw.splitlines(), start=1):
        s = line.strip()
        if s and not s.startswith("#"):
            out.append((i, s))
    return out


def parse_pass_line(s: str):
    """把一行解析成 (date|None, password, used_effective_today)。"""
    day, pw = None, s.strip()
    if ":" in pw:
        head, tail = pw.split(":", 1)
        if _PASS_DATE_RE.match(head.strip()):
            day, pw = head.strip(), tail.strip()
    return day, pw


def delete_pass_line(path: str, lineno: int) -> bool:
    """按行号删除一条口令（原子重写整个文件）。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return False
    if lineno < 1 or lineno > len(lines):
        return False
    del lines[lineno - 1]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))
    os.replace(tmp, path)
    return True

logger = logging.getLogger("accel")

BDUSS_KEY = "bduss"
STOKEN_KEY = "stoken"
PARSE_TTL = 600            # 分享元数据缓存（秒）；不含 dlink，dlink 永不缓存
DLINK_RETRY_MAX = 3        # dlink 过期自动重取上限
DLINK_RETRY_GAP = 30       # 同一任务两次重取的最小间隔（秒）

_parse_cache = {}          # (surl, pwd, account_id) → (ts, ctx)
_parse_lock = threading.Lock()
_retry_state = {}          # task_id → (上次重试 ts, 已试次数)
_qr_session = None         # 当前扫码登录会话（单用户本地工具，保留最近一个）
_qr_lock = threading.Lock()


def _cache_get(key):
    with _parse_lock:
        hit = _parse_cache.get(key)
        if hit and time.time() - hit[0] < PARSE_TTL:
            return hit[1]
        if hit:
            _parse_cache.pop(key, None)
    return None


def _cache_put(key, ctx):
    with _parse_lock:
        if len(_parse_cache) > 50:
            _parse_cache.clear()
        _parse_cache[key] = (time.time(), ctx)


def _safe_subdir(raw: str) -> str:
    """下载子目录白名单清洗：允许 a/b/c 形式；拒绝 ..、空段、盘符与绝对路径。"""
    raw = (raw or "").strip().replace("\\", "/").strip("/")
    if not raw:
        return ""
    parts = raw.split("/")
    if ":" in raw or any(p in ("..", "") for p in parts):
        return ""
    return raw


def _resolve_save_dir(raw: str, default_dir: str) -> str:
    """保存目录解析：空 → 默认目录；绝对路径（盘符或 / 开头）→ 规范化后
    直接使用（拒绝 .. 逃逸）；相对 → 默认目录下的子目录。"""
    raw = (raw or "").strip()
    if not raw:
        return default_dir
    norm = raw.replace("\\", "/").strip()
    is_abs = (len(norm) >= 2 and norm[1] == ":") or norm.startswith("/")
    if is_abs:
        if ".." in [p for p in norm.split("/") if p]:
            return ""
        return os.path.normpath(norm)
    subdir = _safe_subdir(raw)
    return os.path.join(default_dir, subdir) if subdir else default_dir


def register_routes(bp, store, manager, client_cls, cfg_provider=None,
                    cfg_update=None, pool=None, segments=None, scheduler=None,
                    booster=None):

    def _cfg() -> dict:
        return dict(cfg_provider() if cfg_provider else tuning.resolve_config(None))

    # ---------- 内部工具 ----------
    def _client():
        return client_cls(store.get_setting(BDUSS_KEY) or "",
                          store.get_setting(STOKEN_KEY) or "")

    def _client_for(account) -> object:
        """account = None → 主账号；否则用账号池里的凭证。"""
        if not account or account.get("primary") or account.get("id") == 0:
            return _client()
        return client_cls(account["bduss"], account["stoken"])

    def _need_bduss():
        if not (store.get_setting(BDUSS_KEY) or ""):
            return jsonify({"error": "请先在页面设置 BDUSS"}), 400
        return None

    def _resolve_as(account, text: str, pwd_override: str = ""):
        """归一化 →（缓存/网络）resolve，返回 (client, ctx, norm, cache_hit)。

        缓存按账号分键：seckey/shareid 与登录态相关，多账号混用会踩坑。
        """
        norm = normalize_share_text(text)
        pwd = (pwd_override or norm["pwd"]).strip()
        acct_id = (account or {}).get("id", 0)
        key = (norm["surl"], pwd, acct_id)
        ctx = _cache_get(key)
        cache_hit = ctx is not None
        client = _client_for(account)
        if not ctx:
            ctx = client.resolve(norm["surl"], pwd)
            _cache_put(key, ctx)
        return client, ctx, norm, cache_hit

    def _resolve(text: str, pwd_override: str = ""):
        return _resolve_as(None, text, pwd_override)

    def _handle_baidu_error(e: BaiduError):
        code = 400 if e.kind == "pwd" else 502
        return jsonify({"error": e.msg, "kind": e.kind, "errno": e.errno}), code

    # ---------- 访问控制 ----------
    # 访客（非本机、无口令）可用的端点：解析/列目录/直链/配额查询。
    # 站长端点（BDUSS 设置、服务器下载、任务管理）永远只对本机或带口令开放。
    GUEST_PATHS = {"/parse", "/list-dir", "/dlinks", "/guest-quota"}

    def _is_guest() -> bool:
        # 判定规则与主 API 共用一份实现（backend/access.py），避免两处漂移
        return not access.is_station(request)

    @bp.before_request
    def _access_guard():
        if access.is_station(request):
            return None          # 本机 / 持正确令牌 = 站长
        sub = request.path.replace("/api/accel", "", 1) \
            if request.path.startswith("/api/accel") else request.path
        if os.getenv("ACCEL_GUEST", "1") == "1" and sub in GUEST_PATHS:
            # ① 口令校验（服务端）。放在配额之前：口令错就不该消耗额度。
            if sub in PASS_REQUIRED_PATHS:
                st = pass_state(PASS_FILE)
                if st["configured"]:
                    if not st["valid"]:
                        # 配了口令但今天没有有效的行 → 一律拒绝（fail closed）
                        return jsonify({
                            "error": "今日口令未配置或已过期，请联系站长更新",
                            "need_pass": True, "pass_expired": True}), 401
                    supplied = request.headers.get(PASS_HEADER, "") or ""
                    ok, fp = pass_check(PASS_FILE, supplied)
                    if not ok:
                        return jsonify({
                            "error": ("口令不正确或已过期，请向站长索取今天的口令"
                                      if supplied else
                                      "需要解析口令：请输入站长给你的口令"),
                            "need_pass": True,
                        }), 401
                    if fp:
                        store.pass_incr(fp, time.strftime("%Y-%m-%d"))
            # ② 每 IP 每日配额（口令过了也仍然生效，双重限制）
            limit = int(os.getenv("ACCEL_GUEST_DAILY", "20") or "20")
            guest_ip = request.remote_addr or ""
            used = store.guest_count(guest_ip, time.strftime("%Y-%m-%d"))
            if used >= limit:
                return jsonify({"error": f"今日解析次数已用完（每 IP 每天 {limit} 次），"
                                         f"请明天再来"}), 429
            return None  # 计数在各端点动作成功前进行
        return jsonify({"error": "无权访问该功能"}), 403

    # ---------- 状态 / BDUSS ----------
    @bp.route("/status", methods=["GET"])
    def status():
        bduss = store.get_setting(BDUSS_KEY) or ""
        return jsonify({
            "bduss_set": bool(bduss),
            "bduss_hint": mask_secret(bduss),
            "download_dir": manager.download_dir,
            "token_required": bool(os.getenv("ACCEL_TOKEN", "")),
            "aria2": manager.ping(),
            "profile": _cfg().get("profile"),
        })

    @bp.route("/bduss", methods=["PUT"])
    def save_bduss():
        body = request.get_json(silent=True) or {}
        creds = extract_credentials(body.get("bduss", ""))
        bduss = creds["BDUSS"]
        if len(bduss) < 32:
            return jsonify({"error": "BDUSS 格式不正确（应是一串很长的字母数字）"}), 400
        ok, hint = _save_credentials(bduss, creds.get("STOKEN", ""))
        if not ok:
            return jsonify({"error": f"BDUSS 校验未通过：{hint}"}), 400
        return jsonify({"ok": True, "bduss_hint": hint})

    @bp.route("/bduss", methods=["DELETE"])
    def clear_bduss():
        store.delete_setting(BDUSS_KEY)
        store.delete_setting(STOKEN_KEY)
        with _parse_lock:
            _parse_cache.clear()
        logger.info("[加速] BDUSS 已清除")
        return jsonify({"ok": True})

    def _save_credentials(bduss: str, stoken: str = "", extra_jar=None):
        """校验并保存凭证，返回 (ok, 尾号掩码或错误信息)。

        extra_jar：扫码登录时传入二维码会话的完整 cookie jar（含 BAIDUID 等
        预热字段），校验与后续解析与真实浏览器登录态一致。
        """
        client = client_cls(bduss, stoken)
        if extra_jar is not None:
            for c in extra_jar:
                client._sess.cookies.set(c.name, c.value,
                                         domain=c.domain, path=c.path)
        try:
            client.check_login()
        except BaiduError as e:
            detail = f"（百度 errno={e.errno}）" if e.errno is not None else ""
            return False, f"{e.msg}{detail}"
        store.set_setting(BDUSS_KEY, bduss)
        if stoken:
            store.set_setting(STOKEN_KEY, stoken)
        else:
            store.delete_setting(STOKEN_KEY)
        logger.info("[加速] 凭证已保存（尾号 %s%s）", bduss[-4:],
                    "，含 STOKEN" if stoken else "")
        return True, mask_secret(bduss)

    # ---------- 扫码登录 ----------
    @bp.route("/qr/start", methods=["POST"])
    def qr_start():
        global _qr_session
        try:
            sess = QRLoginSession()
            sess.create()
        except QRLoginError as e:
            return jsonify({"error": e.msg}), 502
        except Exception as e:
            logger.warning("[加速] 二维码生成异常: %s", e.__class__.__name__)
            return jsonify({"error": "获取二维码失败，请稍后重试"}), 502
        with _qr_lock:
            _qr_session = sess
        return jsonify({"ok": True, "imgurl": sess.imgurl})

    @bp.route("/qr/status", methods=["GET"])
    def qr_status():
        sess = _qr_session
        if sess is None:
            return jsonify({"status": "none"})
        st = sess.poll()
        resp = {"status": st}
        if st == "confirmed":
            ok, hint = _save_credentials(sess.bduss, sess.stoken,
                                         extra_jar=sess.cookie_jar)
            if ok:
                resp["bduss_hint"] = hint
            else:
                resp.update({"status": "error", "error": hint})
        elif st == "error":
            resp["error"] = sess.error
        return jsonify(resp)

    # ---------- 提速配置 ----------
    @bp.route("/speedup", methods=["GET"])
    def speedup_get():
        cfg = _cfg()
        return jsonify({
            "config": cfg,
            "profiles": {k: {"label": v["label"], "desc": v["desc"],
                             "split": v["split"], "concurrent": v["concurrent"],
                             "min_split": v["min_split"]}
                         for k, v in tuning.PROFILES.items()},
            "defaults": tuning.DEFAULTS,
            "stats": scheduler.view() if scheduler else {},
            "accounts": pool.public_list() if pool else [],
            "limits": {"max_overall_internal": 1},
        })

    @bp.route("/speedup", methods=["PUT"])
    def speedup_put():
        if not cfg_update:
            return jsonify({"error": "提速配置不可用"}), 500
        body = request.get_json(silent=True) or {}
        # 只接受已知键，防止把垃圾字段写进库里
        clean = {}
        for k in tuning.DEFAULTS:
            if k not in body:
                continue
            v = body[k]
            if k in ("profile",):
                if v in tuning.PROFILES:
                    clean[k] = v
            elif k in ("yield_enabled", "ramp_enabled", "segmented",
                       "optimize_concurrent"):
                clean[k] = bool(v)
            else:
                try:
                    clean[k] = max(0, int(v))
                except (TypeError, ValueError):
                    pass
        cfg = cfg_update(clean)
        manager.apply_config(cfg)
        scheduler.note(f"配置已更新：{cfg['profile']} 档 / "
                       f"{cfg['max_concurrent'] or tuning.profile(cfg['profile'])['concurrent']} 并发"
                       f"{' / 多账号分段' if cfg.get('segmented') else ''}")
        logger.info("[加速] 提速配置更新: %s", clean)
        return jsonify({"ok": True, "config": cfg})

    @bp.route("/speedup/diagnose", methods=["GET"])
    def speedup_diagnose():
        cfg = _cfg()
        st = scheduler.view() if scheduler else {}
        accounts = pool.public_list() if pool else []
        usable = pool.count_enabled() if pool else 0
        hints = []
        if st.get("verdict") in ("account_cap", "per_conn_throttle"):
            if usable < 2:
                hints.append("只检测到 1 个可用账号：账号级限速只能靠加第 2 个账号"
                             "（账号池）+ 打开「分段下载」来叠加。")
            else:
                hints.append(f"已检测到 {usable} 个可用账号，建议打开「分段下载」，"
                             f"单文件也会被切成 {usable} 段并行拉。")
        if not cfg.get("yield_enabled", True):
            hints.append("「慢任务让路」当前是关闭的；慢任务会继续占着并发槽，"
                         "打开后才能把槽让给跑得动的任务。")
        if cfg.get("overall_limit"):
            hints.append(f"总带宽上限被设为 {cfg['overall_limit'] / 1e6:.1f} MB/s，"
                         f"想跑满就设为 0。")
        return jsonify({"config": cfg, "stats": st, "accounts": accounts,
                        "usable_accounts": usable, "hints": hints})

    # ---------- 账号池 ----------
    @bp.route("/accounts", methods=["GET"])
    def accounts_list():
        return jsonify({"accounts": pool.public_list() if pool else [],
                        "usable": pool.count_enabled() if pool else 0})

    @bp.route("/accounts", methods=["POST"])
    def accounts_add():
        if not pool:
            return jsonify({"error": "账号池不可用"}), 500
        body = request.get_json(silent=True) or {}
        creds = extract_credentials(body.get("bduss", ""))
        bduss = creds["BDUSS"]
        if len(bduss) < 32:
            return jsonify({"error": "BDUSS 格式不正确"}), 400
        try:
            client_cls(bduss, creds.get("STOKEN", "")).check_login()
        except BaiduError as e:
            detail = f"（errno={e.errno}）" if e.errno is not None else ""
            return jsonify({"error": f"账号校验未通过：{e.msg}{detail}"}), 400
        aid = pool.add(body.get("label", ""), bduss, creds.get("STOKEN", ""))
        logger.info("[加速] 账号池新增 #%s（尾号 %s）", aid, bduss[-4:])
        return jsonify({"ok": True, "id": aid, "hint": mask_secret(bduss)})

    @bp.route("/accounts/<int:account_id>", methods=["PATCH"])
    def accounts_patch(account_id):
        if not pool:
            return jsonify({"error": "账号池不可用"}), 500
        body = request.get_json(silent=True) or {}
        if "enabled" in body:
            pool.set_enabled(account_id, bool(body["enabled"]))
        if body.get("clear_cooldown"):
            pool.clear_cooldown(account_id)
        if body.get("label") is not None:
            store.update_account(account_id, label=str(body["label"])[:40])
        return jsonify({"ok": True, "accounts": pool.public_list()})

    @bp.route("/accounts/<int:account_id>", methods=["DELETE"])
    def accounts_delete(account_id):
        if not pool:
            return jsonify({"error": "账号池不可用"}), 500
        pool.remove(account_id)
        return jsonify({"ok": True, "accounts": pool.public_list()})

    # ---------- 解析 / 目录 ----------
    @bp.route("/parse", methods=["POST"])
    def parse_share():
        err = _need_bduss()
        if err:
            return err
        if _is_guest():
            ip = request.remote_addr or ""
            store.guest_incr(ip, time.strftime("%Y-%m-%d"))
        body = request.get_json(silent=True) or {}
        text = (body.get("url") or "").strip()
        if not text:
            return jsonify({"error": "请粘贴分享链接"}), 400
        try:
            client, ctx, norm, cache_hit = _resolve(text, body.get("pwd") or "")
        except BaiduError as e:
            return _handle_baidu_error(e)
        except Exception as e:
            logger.warning("[加速] 解析异常: %s", e.__class__.__name__)
            return jsonify({"error": "解析失败，请稍后重试"}), 500
        return jsonify({
            "surl": ctx["surl"], "pwd": ctx["pwd"],
            "shareid": ctx["shareid"], "uk": ctx["uk"],
            "cached": cache_hit,
            "files": ctx["root_files"],
        })

    @bp.route("/list-dir", methods=["POST"])
    def list_dir_route():
        err = _need_bduss()
        if err:
            return err
        body = request.get_json(silent=True) or {}
        text = (body.get("url") or "").strip()
        dir_path = (body.get("dir") or "/").strip() or "/"
        if not text:
            return jsonify({"error": "缺少分享链接"}), 400
        try:
            client, ctx, norm, _ = _resolve(text, body.get("pwd") or "")
            files = client.list_dir(ctx, dir_path)
        except BaiduError as e:
            return _handle_baidu_error(e)
        except Exception as e:
            logger.warning("[加速] 目录列表异常: %s", e.__class__.__name__)
            return jsonify({"error": "目录列表获取失败，请稍后重试"}), 500
        return jsonify({"surl": norm["surl"], "dir": dir_path, "files": files})

    # ---------- 访客直链（Kdown 式页面用，不走 aria2 / 不落任务） ----------
    # ---------- 解析口令（实现见模块顶部 pass_entries/pass_check） ----------
    PASS_FILE = os.getenv("ACCEL_PARSE_PASS_FILE") or DEFAULT_PASS_FILE

    def _pass_enabled() -> bool:
        return len(pass_entries(PASS_FILE)) > 0
    def _guest_quota_view() -> dict:
        ip = request.remote_addr or ""
        limit = int(os.getenv("ACCEL_GUEST_DAILY", "20") or "20")
        if not _is_guest():
            today = time.strftime("%Y-%m-%d")
            # 站长额外能看到"今天每条口令各被用了多少次"→ 定位泄露的那条
            return {"unlimited": True, "used": 0, "limit": limit,
                    "remaining": -1, "pass_enabled": _pass_enabled(),
                    "pass_usage": store.pass_usage_today(today)}
        used = store.guest_count(ip, time.strftime("%Y-%m-%d"))
        return {"unlimited": False, "used": used, "limit": limit,
                "remaining": max(0, limit - used),
                "pass_enabled": _pass_enabled()}

    @bp.route("/guest-quota", methods=["GET"])
    def guest_quota():
        return jsonify(_guest_quota_view())

    # ---------- 解析口令管理（仅站长；不在 GUEST_PATHS 里 → 访客自动 403） ----------
    @bp.route("/pass", methods=["GET"])
    def pass_view():
        """今日口令概况 + 每条口令的今日用量（按指纹，不返回明文）。"""
        today = time.strftime("%Y-%m-%d")
        usage = store.pass_usage_today(today)
        items = []
        for lineno, raw in pass_lines(PASS_FILE):
            day, pw = parse_pass_line(raw)
            valid = (day is None or day == today)
            items.append({
                "index": lineno,
                "date": day,                      # None = 永久有效
                "fp": pass_fp(pw),
                "valid_today": valid,
                "used_today": usage.get(pass_fp(pw), 0),
            })
        return jsonify({
            "today": today,
            "file": PASS_FILE,
            "enabled": len(items) > 0,            # 文件里有条目 = 已启用
            "valid_today": sum(1 for it in items if it["valid_today"]),
            "entries": items,
        })

    @bp.route("/pass", methods=["POST"])
    def pass_add():
        """追加一条解析口令。body: {pass?: str, permanent?: bool}

        * 不传 pass → 自动生成一个去掉易混淆字符的口令（10 位）；
        * permanent=true → 不带日期前缀，长期有效（适合给某个朋友单独一条）；
        * 否则写 `YYYY-MM-DD:口令`，只在当天有效。
        明文口令**只在本响应里返回一次**，数据库只存指纹。
        """
        body = request.get_json(silent=True) or {}
        pw = (body.get("pass") or "").strip()
        permanent = bool(body.get("permanent"))
        if pw:
            if len(pw) < 4:
                return jsonify({"error": "口令太短（至少 4 位）"}), 400
            if ":" in pw or "\n" in pw or "\r" in pw:
                return jsonify({"error": "口令不能包含冒号或换行"}), 400
        else:
            pw = gen_pass()
        day = "" if permanent else time.strftime("%Y-%m-%d")
        line = f"{day}:{pw}" if day else pw

        os.makedirs(os.path.dirname(PASS_FILE) or ".", exist_ok=True)
        try:
            with open(PASS_FILE, "r", encoding="utf-8") as f:
                cur = f.read()
        except OSError:
            cur = ""
        with open(PASS_FILE, "a", encoding="utf-8") as f:
            if cur and not cur.endswith("\n"):
                f.write("\n")          # 避免与上一行粘连
            f.write(line + "\n")
        logger.info("[加速] 追加解析口令：%s", "永久" if permanent else day)
        return jsonify({"ok": True, "pass": pw, "permanent": permanent,
                        "date": day or None, "file": PASS_FILE})

    @bp.route("/pass", methods=["DELETE"])
    def pass_del():
        """按行号吊销一条口令（?index=N，取自 GET 的 entries[].index）。"""
        try:
            idx = int(request.args.get("index", ""))
        except (TypeError, ValueError):
            return jsonify({"error": "缺少 index"}), 400
        if not delete_pass_line(PASS_FILE, idx):
            return jsonify({"error": f"没有第 {idx} 行"}), 404
        logger.info("[加速] 吊销解析口令：第 %d 行", idx)
        return jsonify({"ok": True, "deleted_index": idx})

    @bp.route("/dlinks", methods=["POST"])
    def guest_dlinks():
        if not _is_guest():
            pass  # 站长请求不占访客配额
        else:
            ip = request.remote_addr or ""
            store.guest_incr(ip, time.strftime("%Y-%m-%d"))
        err = _need_bduss()
        if err:
            return err
        body = request.get_json(silent=True) or {}
        text = (body.get("url") or "").strip()
        items = [it for it in (body.get("items") or [])
                 if it.get("fid") and it.get("name") and not it.get("is_dir")]
        if not text:
            return jsonify({"error": "缺少分享链接"}), 400
        if not items:
            return jsonify({"error": "缺少文件"}), 400
        try:
            client, ctx, norm, _ = _resolve(text, body.get("pwd") or "")
            dlinks = client.fetch_dlinks(
                ctx, [str(it["fid"]) for it in items])
        except BaiduError as e:
            return _handle_baidu_error(e)
        except Exception as e:
            logger.warning("[加速] 访客直链异常: %s", e.__class__.__name__)
            return jsonify({"error": "直链获取失败，请稍后重试"}), 500
        by_fid = {d["fs_id"]: d for d in dlinks}
        out = []
        for it in items:
            d = by_fid.get(str(it["fid"]))
            if d:
                out.append({"name": d["filename"] or it["name"],
                            "size": d["size"] or int(it.get("size") or 0),
                            "dlink": d["dlink"]})
        if not out:
            return jsonify({"error": "未能取得直链，该分享可能禁止下载"}), 502
        resp = {"ok": True, "links": out, "quota": _guest_quota_view()}
        return jsonify(resp)

    # ---------- 下载 ----------
    def _account_rotation(n: int) -> list:
        """按可用账号数给出轮转顺序；只有主账号时返回 [None]*n。"""
        if not pool or pool.count_enabled() <= 1:
            return [None] * n
        picked = pool.pick(n)
        if len(picked) < 2:
            return [None] * n
        return [picked[i % len(picked)] for i in range(n)]

    @bp.route("/download", methods=["POST"])
    def start_download():
        err = _need_bduss()
        if err:
            return err
        body = request.get_json(silent=True) or {}
        text = (body.get("url") or "").strip()
        pwd_in = body.get("pwd") or ""
        items = [it for it in (body.get("items") or [])
                 if it.get("fid") and it.get("name") and not it.get("is_dir")]
        if not text:
            return jsonify({"error": "缺少分享链接"}), 400
        if not items:
            return jsonify({"error": "请勾选要下载的文件"}), 400

        save_dir = _resolve_save_dir(body.get("save_dir") or "",
                                     manager.download_dir)
        if not save_dir:
            return jsonify({"error": "保存路径不合法（含 \"..\" 或格式错误）"}), 400
        try:
            os.makedirs(save_dir, exist_ok=True)
        except OSError:
            return jsonify({"error": f"下载目录不可写：{save_dir}"}), 400

        cfg = _cfg()
        # 先验证链接可用（同时把元数据塞进缓存，后续每个账号复用）
        try:
            _, ctx, norm, _ = _resolve(text, pwd_in)
        except BaiduError as e:
            return _handle_baidu_error(e)
        except Exception as e:
            logger.warning("[加速] 直链获取异常: %s", e.__class__.__name__)
            return jsonify({"error": "直链获取失败，请稍后重试"}), 500

        created, errors, groups = [], [], []

        # ① 分段跨账号（可选）：大文件切成 K 段，K = 可用账号数
        if pool and segments and cfg.get("segmented") and pool.count_enabled() > 1:
            def _ctx_builder(item, acct):
                client = _client_for(acct)
                c = _resolve_as(acct, text, pwd_in)[1]
                return c, f"{PAN}/s/{c['surl']}", client

            seg_created, seg_errors, groups = segments.submit(
                items, save_dir, _ctx_builder)
            errors.extend(seg_errors)
            plain_items = [c["item"] for c in seg_created if c["kind"] == "single"]
            for c in seg_created:
                if c["kind"] == "group":
                    created.append({"id": c["head_task_id"],
                                    "name": c["item"]["name"],
                                    "gid": "", "segments": c["group"]["seg_total"]})
        else:
            plain_items = items

        # ② 普通多连接下载；多账号时按文件轮转分流（账号级限速下按文件叠加）
        rot = _account_rotation(len(plain_items)) if plain_items else []
        for it, acct in zip(plain_items, rot):
            try:
                client = _client_for(acct)
                ctx_i = _resolve_as(acct, text, pwd_in)[1]
                dlinks = client.fetch_dlinks(ctx_i, [str(it["fid"])])
            except BaiduError as e:
                if pool:
                    pool.report((acct or {}).get("id", 0), False, e.errno, e.msg)
                errors.append(f"{it.get('name', '')}：{e.msg}")
                continue
            except Exception as e:
                logger.warning("[加速] 直链获取异常: %s", e.__class__.__name__)
                errors.append(f"{it.get('name', '')}：直链获取失败")
                continue
            if pool:
                pool.report((acct or {}).get("id", 0), True)
            d = dlinks[0]
            name = d["filename"] or it["name"]
            referer = f"{PAN}/s/{ctx_i['surl']}"
            try:
                gid = manager.add_download(d["dlink"], name, save_dir,
                                           client.cookie_header(), referer)
            except Exception as e:
                logger.warning("[加速] aria2 推送失败: %s", e.__class__.__name__)
                return jsonify({"error": f"推送到 aria2 失败：{e}"}), 500
            task_id = store.create_task(
                name=name, size=d["size"] or int(it.get("size") or 0),
                save_dir=save_dir, fid=str(it["fid"]),
                file_path=it.get("path", ""),
                ctx={**ctx_i, "gid": gid},
                account_id=(acct or {}).get("id", 0))
            created.append({"id": task_id, "name": name, "gid": gid})

        if not created:
            msg = "；".join(errors[:3]) if errors else \
                "未能取得任何文件直链，该分享可能禁止下载"
            return jsonify({"error": msg}), 502
        resp = {"ok": True, "tasks": created}
        if errors:
            resp["skipped"] = len(errors)
            resp["warnings"] = errors[:5]
        if scheduler and created:
            scheduler.note(f"新增 {len(created)} 个任务"
                           f"{'（' + str(len(groups)) + ' 个多账号分段）' if groups else ''}")
        return jsonify(resp)

    # ---------- 任务列表 / 控制 ----------
    def _snapshot() -> dict:
        """一次性拉取 aria2 活跃/排队状态，避免每个任务一次 RPC。"""
        snap = {}
        for group in (manager.tell_active(), manager.tell_waiting(0, 500)):
            for a in group:
                snap[a.get("gid")] = {
                    "status": a.get("status", ""),
                    "completed": int(a.get("completedLength") or 0),
                    "total": int(a.get("totalLength") or 0),
                    "speed": int(a.get("downloadSpeed") or 0),
                    "conns": int(a.get("connections") or 0),
                    "error": a.get("errorMessage") or "",
                }
        return snap

    @bp.route("/tasks", methods=["GET"])
    def list_tasks():
        snap = _snapshot()
        sched_state = scheduler.per_task() if scheduler else {}
        rows = store.list_tasks(limit=200, include_hidden=False)
        out = []
        aria2_online = manager.ping()["online"]

        for t in rows:
            t = dict(t)
            view = {"id": t["id"], "name": t["name"], "size": t["size"],
                    "save_dir": t["save_dir"], "status": t["status"],
                    "error": t["error"], "created_at": t["created_at"],
                    "priority": t.get("priority", 0),
                    "group_id": t.get("group_id", ""),
                    "segments": t.get("seg_total", 0) if t.get("group_id") else 0}

            gids = [(t["aria2_gid"], t.get("seg_index", -1))]
            if t.get("group_id"):
                # 分段组：头任务负责聚合展示；汇总所有段的进度与速度
                parts = store.group_tasks(t["group_id"])
                gids = [(p["aria2_gid"], p["seg_index"]) for p in parts]

            completed = 0
            speed = 0
            conns = 0
            total = t["size"]
            errs = []
            live = False
            for gid, _idx in gids:
                if not gid:
                    continue
                s = snap.get(gid)
                if s is None and t["status"] in ("downloading", "paused"):
                    try:
                        s = manager.status(gid)
                        s["conns"] = s.pop("connections", 0)
                    except Exception:
                        aria2_online = False
                        continue
                if not s:
                    continue
                completed += s["completed"]
                speed += s["speed"]
                conns += s.get("conns", 0)
                if s["status"] == "active":
                    live = True
                if s["status"] in ("error", "removed") and s.get("error"):
                    errs.append(s["error"])

            if t["status"] in ("downloading", "paused") and gids:
                if live:
                    st = "downloading"
                elif errs:
                    if _maybe_refresh_dlink(t, errs[0]):
                        st = "downloading"
                        errs = []
                    else:
                        st = "error"
                        store.update_task(t["id"], status="error", error=errs[0])
                elif t["status"] == "paused":
                    st = "paused"
                else:
                    # aria2 侧没在跑：可能是被调度器让路，也可能是刚推完还没起来
                    st = "queued" if t["id"] in sched_state else "paused"
                view.update(status=st, completed=completed, speed=speed,
                            connections=conns)
                if st == "error":
                    view.update(error=errs[0] if errs else t["error"])
                else:
                    view["error"] = ""
                if t["status"] != st and st in ("error",):
                    pass
            else:
                if t["status"] == "done":
                    completed = total or completed
                view.update(completed=completed, speed=0)
            if t["id"] in sched_state and sched_state[t["id"]].get("deferred"):
                view["yielded"] = True
                view["yield_count"] = sched_state[t["id"]].get("deferred_count", 0)
            if t["id"] in sched_state and sched_state[t["id"]].get("split"):
                view["split"] = sched_state[t["id"]]["split"]
            out.append(view)

        return jsonify({"tasks": out, "aria2_online": aria2_online,
                        "download_dir": manager.download_dir,
                        "speedup": scheduler.view() if scheduler else {}})

    def _maybe_refresh_dlink(t: dict, err_msg: str) -> bool:
        """dlink 过期（HTTP 403/410）：按任务存的分享元数据重取一次并续传。"""
        if not any(c in err_msg for c in ("403", "410")):
            return False
        last_ts, cnt = _retry_state.get(t["id"], (0, 0))
        now = time.time()
        if cnt >= DLINK_RETRY_MAX or now - last_ts < DLINK_RETRY_GAP:
            return False
        _retry_state[t["id"]] = (now, cnt + 1)
        acct_id = t.get("account_id", 0) if hasattr(t, "get") else 0
        acct = None
        if pool and acct_id:
            row = store.get_account(acct_id)
            if row:
                acct = {"id": row["id"], "bduss": row["bduss"],
                        "stoken": row["stoken"]}
        client = _client_for(acct)
        try:
            ctx = {"surl": t["surl"], "pwd": t["pwd"], "shareid": t["shareid"],
                   "uk": t["uk"], "sign": t["sign"], "timestamp": t["timestamp"]}
            d = client.fetch_dlinks(ctx, [t["fid"]])[0]
            opts = None
            if t.get("part_out"):
                opts = {"split": "1", "max-connection-per-server": "1",
                        "continue": "true", "allow-overwrite": "true",
                        "auto-file-renaming": "false",
                        "header": [f"Range: bytes={t['part_start']}-{t['part_end']}"]}
            gid = manager.add_download(
                d["dlink"], t["part_out"] or t["name"], t["save_dir"],
                client.cookie_header(), f"{PAN}/s/{t['surl']}", options=opts)
            store.update_task(t["id"], aria2_gid=gid, status="downloading",
                              error="")
            logger.info("[加速] 直链过期已自动重取并续传（任务 %s）", t["id"])
            return True
        except Exception as e:
            logger.warning("[加速] 直链重取失败（任务 %s）: %s",
                           t["id"], e.__class__.__name__)
            return False

    @bp.route("/tasks/<int:task_id>/pause", methods=["POST"])
    def pause_task(task_id):
        t = store.get_task(task_id)
        if not t:
            return jsonify({"error": "任务不存在"}), 404
        if scheduler:
            scheduler.mark_user_paused(task_id)
        try:
            if t["aria2_gid"]:
                manager.pause(t["aria2_gid"])
        except Exception as e:
            return jsonify({"error": f"暂停失败：{e}"}), 500
        store.update_task(task_id, status="paused")
        return jsonify({"ok": True})

    @bp.route("/tasks/<int:task_id>/resume", methods=["POST"])
    def resume_task(task_id):
        t = store.get_task(task_id)
        if not t:
            return jsonify({"error": "任务不存在"}), 404
        if scheduler:
            scheduler.mark_user_resumed(task_id)
        try:
            if t["aria2_gid"]:
                manager.resume(t["aria2_gid"])
        except Exception as e:
            return jsonify({"error": f"恢复失败：{e}"}), 500
        store.update_task(task_id, status="downloading", error="")
        return jsonify({"ok": True})

    @bp.route("/tasks/<int:task_id>/priority", methods=["POST"])
    def priority_task(task_id):
        """置顶/取消置顶：让路时优先牺牲别人，保住这个任务。"""
        t = store.get_task(task_id)
        if not t:
            return jsonify({"error": "任务不存在"}), 404
        body = request.get_json(silent=True) or {}
        val = 1 if body.get("priority", 1) else 0
        store.update_task(task_id, priority=val)
        if scheduler:
            scheduler.note(f"{'置顶' if val else '取消置顶'}：{t['name'][:28]}")
        return jsonify({"ok": True, "priority": val})

    @bp.route("/tasks/<int:task_id>", methods=["DELETE"])
    def delete_task(task_id):
        t = store.get_task(task_id)
        if not t:
            return jsonify({"error": "任务不存在"}), 404
        targets = [t]
        if t["group_id"]:
            targets = store.group_tasks(t["group_id"])
        for row in targets:
            try:
                if row["aria2_gid"] and row["status"] in ("downloading", "paused"):
                    manager.cancel(row["aria2_gid"])
            except Exception as e:
                logger.warning("[加速] 取消 aria2 任务失败: %s", e.__class__.__name__)
            if scheduler:
                scheduler.forget(row["id"])
            store.delete_task(row["id"])
        _retry_state.pop(task_id, None)
        return jsonify({"ok": True})

    # ---------- 客户端加速（Booster：自研选择性 Hook，站长专属） ----------
    @bp.route("/booster/status", methods=["GET"])
    def booster_status():
        if not booster:
            return jsonify({"available": False,
                            "reason": "加速模块未初始化"})
        ok, reason = booster.dlls_ready()
        if not ok:
            return jsonify({"available": False, "reason": reason})
        return jsonify({"available": True, "reason": "", **booster.status()})

    @bp.route("/booster/inject", methods=["POST"])
    def booster_inject():
        if not booster:
            return jsonify({"error": "booster 不可用"}), 500
        body = request.get_json(silent=True) or {}
        try:
            if body.get("pid"):
                msg = booster.inject(int(body["pid"]))
                return jsonify({"ok": True, "message": msg})
            r = booster.inject_all()
            return jsonify({"ok": True, **r})
        except OpenSpeedyHookConflict as e:
            return jsonify({"error": str(e)}), 409
        except Exception as e:
            return jsonify({"error": f"{e}"}), 500

    @bp.route("/booster/inject-engine", methods=["POST"])
    def booster_inject_engine():
        """只注入真下载引擎进程（按 kernel.dll 判定）——**推荐入口**。

        /booster/inject 不带 pid 时走 inject_all()（全部百度进程，含 CEF
        渲染进程），会把 UI 进程的时钟也一起缩放，不是我们要的。
        """
        if not booster:
            return jsonify({"error": "booster 不可用"}), 500
        try:
            return jsonify({"ok": True, **booster.inject_engine()})
        except OpenSpeedyHookConflict as e:
            return jsonify({"error": str(e)}), 409
        except Exception as e:
            return jsonify({"error": f"{e}"}), 500

    @bp.route("/booster/eject", methods=["POST"])
    def booster_eject():
        if not booster:
            return jsonify({"error": "booster 不可用"}), 500
        return jsonify({"ok": True, "ejected": booster.eject_all()})

    @bp.route("/booster/factor", methods=["PUT"])
    def booster_factor():
        if not booster:
            return jsonify({"error": "booster 不可用"}), 500
        body = request.get_json(silent=True) or {}
        try:
            f = float(body.get("factor", 5))
        except (TypeError, ValueError):
            return jsonify({"error": "倍率格式不正确"}), 400
        if not (1 <= f <= 16):
            return jsonify({"error": "倍率需在 1~16 之间"}), 400
        booster.stop_auto()          # 手动覆盖：暂停闭环
        booster.set_factor(f)
        booster.set_enabled(True)
        return jsonify({"ok": True, "factor": f})

    @bp.route("/booster/enabled", methods=["PUT"])
    def booster_enabled():
        """开启/关闭加速（hook 保留在进程里，只是把 enabled 置 0/1）。

        这是"关掉提速"的正确做法：不卸载 DLL，随时能再开；要彻底卸载/干净
        解除请用 eject（注意 eject 有已知残留问题，最稳是重启客户端）。
        """
        if not booster:
            return jsonify({"error": "booster 不可用"}), 500
        body = request.get_json(silent=True) or {}
        on = bool(body.get("enabled", True))
        if on:
            booster.stop_auto()      # 手动开启即视为接管闭环
        booster.set_enabled(on)
        return jsonify({"ok": True, "enabled": on,
                        "state": booster.status().get("state")})

    @bp.route("/booster/auto", methods=["PUT"])
    def booster_auto():
        if not booster:
            return jsonify({"error": "booster 不可用"}), 500
        body = request.get_json(silent=True) or {}
        if body.get("enabled"):
            try:
                target = float(body.get("target", 8))
            except (TypeError, ValueError):
                target = 8.0
            booster.start_auto(target)
        else:
            booster.stop_auto()
        return jsonify({"ok": True, "auto": booster._auto,
                        "state": booster._state})
