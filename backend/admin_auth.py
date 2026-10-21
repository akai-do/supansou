"""后台会话判定与鉴权装饰器。"""
from functools import wraps

from flask import jsonify, session

from config import Config


def admin_enabled() -> bool:
    return bool(Config.ADMIN_V2)


def current_user():
    username = session.get("admin_username")
    if not username:
        return None
    return {
        "username": username,
        "role": session.get("admin_role", "admin"),
        "must_change_password": bool(session.get("must_change_password")),
    }


def require_admin(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not admin_enabled():
            return jsonify({"error": "管理后台未开启"}), 403
        user = current_user()
        if not user:
            return jsonify({"error": "请先登录"}), 401
        if user["must_change_password"]:
            return jsonify({"error": "请先修改初始密码"}), 403
        return fn(*args, **kwargs)
    return wrapper
