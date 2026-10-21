"""管理后台认证：登录 / 当前用户 / 登出 / 修改密码。"""
import logging
import threading
import time

from flask import Blueprint, jsonify, request, session

import admin_store
from admin_auth import admin_enabled, current_user
from config import Config

logger = logging.getLogger("api.auth")
auth_bp = Blueprint("auth", __name__)

_LOGIN_LOCK = threading.Lock()
_LOGIN_ATTEMPTS = {}
_LOGIN_LIMIT = 10
_LOGIN_WINDOW = 300  # 5 分钟


def _allow_login(ip: str) -> bool:
    now = time.time()
    key = ip or "?"
    with _LOGIN_LOCK:
        arr = [t for t in _LOGIN_ATTEMPTS.get(key, []) if now - t < _LOGIN_WINDOW]
        if len(arr) >= _LOGIN_LIMIT:
            _LOGIN_ATTEMPTS[key] = arr
            return False
        arr.append(now)
        _LOGIN_ATTEMPTS[key] = arr
        return True


def _clear_login(ip: str) -> None:
    with _LOGIN_LOCK:
        _LOGIN_ATTEMPTS.pop(ip or "?", None)


def register_auth_routes(app) -> None:
    admin_store.ensure_schema()
    created = admin_store.ensure_default_admin(
        Config.ADMIN_USERNAME, Config.ADMIN_PASSWORD
    )
    if created:
        # 只提示初始化完成，绝不输出密码
        logger.info("已创建默认管理员 %r，首次登录需修改密码",
                    Config.ADMIN_USERNAME)

    if "auth" in app.blueprints:
        return

    @auth_bp.route("/auth/login", methods=["POST"])
    def login():
        if not admin_enabled():
            return jsonify({"error": "管理后台未开启"}), 403
        ip = request.remote_addr or ""
        if not _allow_login(ip):
            return jsonify({"error": "登录尝试过于频繁，请 5 分钟后再试"}), 429

        body = request.get_json(silent=True) or {}
        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "")
        if not username or not password:
            return jsonify({"error": "请输入用户名和密码"}), 400

        if admin_store.count_users() == 0:
            return jsonify({
                "error": "后台未初始化：请设置 ADMIN_PASSWORD 环境变量并重启后端"
            }), 503

        user = admin_store.verify_login(username, password)
        if not user:
            logger.warning("[后台] 登录失败 username=%r ip=%s", username[:40], ip)
            return jsonify({"error": "用户名或密码不正确"}), 401

        _clear_login(ip)
        session.clear()
        session["admin_username"] = user["username"]
        session["admin_role"] = user["role"]
        session["must_change_password"] = bool(user["must_change_password"])
        session.permanent = True
        admin_store.touch_login(user["username"])
        admin_store.audit(user["username"], "login", "", f"ip={ip}")
        return jsonify({
            "ok": True,
            "user": {
                "username": user["username"],
                "role": user["role"],
                "must_change_password": bool(user["must_change_password"]),
            },
        })

    @auth_bp.route("/auth/me", methods=["GET"])
    def me():
        if not admin_enabled():
            return jsonify({"error": "管理后台未开启"}), 403
        user = current_user()
        if not user:
            return jsonify({"error": "请先登录"}), 401
        # 确认账号仍然存在（被删号后会话立即失效）
        row = admin_store.get_user(user["username"])
        if not row:
            session.clear()
            return jsonify({"error": "账号不存在"}), 401
        return jsonify({
            "user": {
                "username": row["username"],
                "role": row["role"],
                "must_change_password": bool(row["must_change_password"]),
            },
        })

    @auth_bp.route("/auth/logout", methods=["POST"])
    def logout():
        if not admin_enabled():
            return jsonify({"error": "管理后台未开启"}), 403
        user = current_user()
        if user:
            admin_store.audit(user["username"], "logout")
        session.clear()
        return jsonify({"ok": True})

    @auth_bp.route("/auth/change-password", methods=["POST"])
    def change_password():
        if not admin_enabled():
            return jsonify({"error": "管理后台未开启"}), 403
        user = current_user()
        if not user:
            return jsonify({"error": "请先登录"}), 401

        body = request.get_json(silent=True) or {}
        old_password = str(body.get("old_password") or "")
        new_password = str(body.get("new_password") or "")
        if not old_password or not new_password:
            return jsonify({"error": "请填写原密码和新密码"}), 400
        if len(new_password) < 8:
            return jsonify({"error": "新密码至少 8 位"}), 400
        if new_password == old_password:
            return jsonify({"error": "新密码不能与原密码相同"}), 400

        row = admin_store.get_user(user["username"])
        if not row or not admin_store.verify_login(user["username"], old_password):
            return jsonify({"error": "原密码不正确"}), 401

        admin_store.change_password(user["username"], new_password)
        session["must_change_password"] = False
        admin_store.audit(user["username"], "change_password")
        return jsonify({"ok": True})

    app.register_blueprint(auth_bp, url_prefix="/api")
