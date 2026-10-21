"""管理后台接口：资源、需求、设置、审计。

所有接口都要求后台会话（与 ACCEL_TOKEN 分离），并且受 ADMIN_V2 总开关控制。
"""
import logging
import re

from flask import Blueprint, jsonify, request

import admin_store
import demand_store
import site_config
from admin_auth import current_user, require_admin
from config import Config

logger = logging.getLogger("api.admin")
admin_bp = Blueprint("admin", __name__)

SETTINGS_KEYS = (
    "site_name", "site_subtitle", "site_logo", "footer_note", "repo_url",
    "notice_enabled", "notice_text", "notice_version", "legal_footer",
    "demand_enabled",
)
_BOOL_KEYS = {"notice_enabled", "legal_footer", "demand_enabled"}
_LIMITS = {
    "site_name": 40,
    "site_subtitle": 60,
    "site_logo": 200,
    "footer_note": 300,
    "repo_url": 200,
    "notice_text": 1000,
    "notice_version": 40,
}


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _validate_resource(data: dict):
    if not isinstance(data, dict):
        return None, "资源格式不正确"
    url = str(data.get("url") or "").strip()
    if not re.match(r"^(https?://|magnet:\?|ed2k://)", url, re.IGNORECASE):
        return None, "链接只支持 http(s)、magnet 或 ed2k"
    if len(url) > 2000:
        return None, "链接过长"
    title = re.sub(r"\s+", " ", str(data.get("title") or "")).strip()
    if not title:
        return None, "标题不能为空"
    if len(title) > 120:
        return None, "标题最多 120 个字"
    disk_type = str(data.get("disk_type") or "").strip().lower()
    if disk_type and not re.match(r"^[0-9a-z_-]{1,20}$", disk_type):
        return None, "网盘类型格式不正确"
    password = str(data.get("password") or "").strip()
    if len(password) > 40:
        return None, "提取码过长"
    source = str(data.get("source") or "").strip()
    if len(source) > 80:
        return None, "来源最多 80 个字"
    return {
        "url": url,
        "title": title,
        "disk_type": disk_type or "others",
        "password": password,
        "source": source or "admin",
    }, None


def _validate_setting(key: str, value):
    if key in _BOOL_KEYS:
        return ("1" if _truthy(value) else "0"), None
    text = str(value if value is not None else "").strip()
    limit = _LIMITS.get(key, 300)
    if len(text) > limit:
        return None, f"{key} 最多 {limit} 个字符"
    if key == "site_name" and not text:
        return None, "站点名称不能为空"
    if key == "site_logo" and text and not (
        text.startswith("/") or re.match(r"^https?://", text, re.IGNORECASE)
    ):
        return None, "Logo 地址只支持站内路径或 http(s)"
    if key == "repo_url" and text and not re.match(
        r"^https?://", text, re.IGNORECASE
    ):
        return None, "仓库地址只支持 http(s)"
    return text, None


def register_admin_routes(app, index_db) -> None:
    admin_store.ensure_schema()
    demand_store.ensure_schema()
    site_config.ensure_schema()
    if "admin" in app.blueprints:
        return

    def _user_name() -> str:
        user = current_user() or {}
        return user.get("username", "")

    # ---------------- 控制台辅助 ----------------
    @admin_bp.route("/admin/audit", methods=["GET"])
    @require_admin
    def audit_list():
        try:
            limit = request.args.get("limit", "20")
            return jsonify({"items": admin_store.list_audit(limit)})
        except Exception as e:
            logger.error("读取审计日志失败: %s", e)
            return jsonify({"error": "读取审计日志失败"}), 500

    # ---------------- 资源管理 ----------------
    @admin_bp.route("/admin/resources", methods=["GET"])
    @require_admin
    def resources_list():
        try:
            data = index_db.admin_list(
                keyword=request.args.get("kw", ""),
                disk_type=request.args.get("disk_type", ""),
                include_dead=request.args.get("include_dead", "1") != "0",
                page=request.args.get("page", "1"),
                page_size=request.args.get("page_size", "50"),
            )
            return jsonify(data)
        except Exception as e:
            logger.error("后台资源列表失败: %s", e)
            return jsonify({"error": "资源列表读取失败"}), 500

    @admin_bp.route("/admin/resources", methods=["POST"])
    @require_admin
    def resource_add():
        body = request.get_json(silent=True) or {}
        item, error = _validate_resource(body)
        if error:
            return jsonify({"error": error}), 400
        try:
            processed = index_db.store_links([item])
            admin_store.audit(_user_name(), "resource_add", item["url"],
                              item["title"][:80])
            return jsonify({"ok": True, "processed": processed})
        except Exception as e:
            logger.error("后台新增资源失败: %s", e)
            return jsonify({"error": "新增资源失败"}), 500

    @admin_bp.route("/admin/resources/import", methods=["POST"])
    @require_admin
    def resource_import():
        body = request.get_json(silent=True) or {}
        items = body.get("items")
        if not isinstance(items, list) or not items:
            return jsonify({"error": "请提供 items 数组"}), 400
        if len(items) > 500:
            return jsonify({"error": "单次最多导入 500 条"}), 400

        valid, errors = [], []
        for idx, raw in enumerate(items):
            item, error = _validate_resource(raw)
            if error:
                errors.append({"index": idx, "error": error})
            else:
                valid.append(item)
        try:
            processed = index_db.store_links(valid) if valid else 0
            admin_store.audit(
                _user_name(), "resource_import", f"{len(valid)} items",
                f"processed={processed}, skipped={len(errors)}",
            )
            return jsonify({
                "ok": True,
                "processed": processed,
                "skipped": len(errors),
                "errors": errors[:20],
            })
        except Exception as e:
            logger.error("后台导入资源失败: %s", e)
            return jsonify({"error": "导入资源失败"}), 500

    @admin_bp.route("/admin/resources/batch-delete", methods=["POST"])
    @require_admin
    def resource_batch_delete():
        body = request.get_json(silent=True) or {}
        urls = body.get("urls")
        if not isinstance(urls, list) or not urls:
            return jsonify({"error": "请提供 urls 数组"}), 400
        if len(urls) > 200:
            return jsonify({"error": "单次最多删除 200 条"}), 400
        try:
            deleted = index_db.admin_delete_urls(urls)
            admin_store.audit(_user_name(), "resource_delete",
                              f"{len(urls)} urls", f"deleted={deleted}")
            return jsonify({"ok": True, "deleted": deleted})
        except Exception as e:
            logger.error("后台批量删除资源失败: %s", e)
            return jsonify({"error": "批量删除失败"}), 500

    # ---------------- 需求管理 ----------------
    @admin_bp.route("/admin/demands", methods=["GET"])
    @require_admin
    def demands_list():
        try:
            data = demand_store.list_admin(
                status=request.args.get("status", ""),
                page=request.args.get("page", "1"),
                page_size=request.args.get("page_size", "20"),
            )
            return jsonify(data)
        except Exception as e:
            logger.error("后台需求列表失败: %s", e)
            return jsonify({"error": "需求列表读取失败"}), 500

    @admin_bp.route("/admin/demands/<int:demand_id>", methods=["PUT"])
    @require_admin
    def demand_update(demand_id: int):
        body = request.get_json(silent=True) or {}
        status = str(body.get("status") or "").strip()
        if status not in ("pending", "resolved", "rejected"):
            return jsonify({"error": "状态不合法"}), 400
        try:
            ok = demand_store.update_admin(
                demand_id, status,
                resolve_note=body.get("resolve_note", ""),
                reject_reason=body.get("reject_reason", ""),
            )
            if not ok:
                return jsonify({"error": "需求不存在"}), 404
            admin_store.audit(_user_name(), "demand_update",
                              f"demand#{demand_id}", f"status={status}")
            return jsonify({"ok": True})
        except Exception as e:
            logger.error("后台更新需求失败: %s", e)
            return jsonify({"error": "更新需求失败"}), 500

    @admin_bp.route("/admin/demands/<int:demand_id>", methods=["DELETE"])
    @require_admin
    def demand_delete(demand_id: int):
        try:
            ok = demand_store.soft_delete(demand_id)
            if not ok:
                return jsonify({"error": "需求不存在"}), 404
            admin_store.audit(_user_name(), "demand_delete",
                              f"demand#{demand_id}")
            return jsonify({"ok": True})
        except Exception as e:
            logger.error("后台删除需求失败: %s", e)
            return jsonify({"error": "删除需求失败"}), 500

    # ---------------- 需求评论管理 ----------------
    @admin_bp.route("/admin/demands/<int:demand_id>/comments", methods=["GET"])
    @require_admin
    def demand_comments_list(demand_id: int):
        try:
            include_hidden = request.args.get("include_hidden", "1") != "0"
            data = demand_store.list_comments(
                demand_id, include_hidden=include_hidden
            )
            if data.get("error"):
                return jsonify({"error": data["error"]}), data.get("status", 404)
            return jsonify(data)
        except Exception as e:
            logger.error("后台读取需求评论失败: %s", e)
            return jsonify({"error": "评论读取失败"}), 500

    @admin_bp.route("/admin/demands/<int:demand_id>/comments", methods=["POST"])
    @require_admin
    def demand_comment_reply(demand_id: int):
        body = request.get_json(silent=True) or {}
        clean, error = demand_store.validate_comment(body)
        if error:
            return jsonify({"error": error}), 400
        clean["nickname"] = _user_name() or "站长"
        try:
            result = demand_store.create_comment(
                demand_id, clean, "", is_admin=True
            )
            if result.get("error"):
                return jsonify({"error": result["error"]}), result.get("status", 404)
            admin_store.audit(_user_name(), "demand_comment_reply",
                              f"demand#{demand_id}", clean["content"][:60])
            return jsonify({"ok": True, **result}), 201
        except Exception as e:
            logger.error("后台回复需求评论失败: %s", e)
            return jsonify({"error": "回复失败"}), 500

    @admin_bp.route("/admin/comments/<int:comment_id>", methods=["PUT"])
    @require_admin
    def demand_comment_update(comment_id: int):
        body = request.get_json(silent=True) or {}
        status = str(body.get("status") or "").strip()
        if status not in ("visible", "hidden"):
            return jsonify({"error": "状态不合法"}), 400
        try:
            ok = demand_store.set_comment_status(comment_id, status)
            if not ok:
                return jsonify({"error": "评论不存在"}), 404
            admin_store.audit(_user_name(), "demand_comment_update",
                              f"comment#{comment_id}", f"status={status}")
            return jsonify({"ok": True})
        except Exception as e:
            logger.error("后台更新评论状态失败: %s", e)
            return jsonify({"error": "更新评论失败"}), 500

    @admin_bp.route("/admin/comments/<int:comment_id>", methods=["DELETE"])
    @require_admin
    def demand_comment_delete(comment_id: int):
        try:
            ok = demand_store.delete_comment(comment_id)
            if not ok:
                return jsonify({"error": "评论不存在"}), 404
            admin_store.audit(_user_name(), "demand_comment_delete",
                              f"comment#{comment_id}")
            return jsonify({"ok": True})
        except Exception as e:
            logger.error("后台删除评论失败: %s", e)
            return jsonify({"error": "删除评论失败"}), 500

    # ---------------- 站点设置 ----------------
    @admin_bp.route("/admin/settings", methods=["GET"])
    @require_admin
    def settings_get():
        try:
            settings = site_config.get_settings()
            values = {k: settings.get(k, "") for k in SETTINGS_KEYS}
            # 二级开关未写入时，页面显示总闸的默认状态
            if not values.get("demand_enabled"):
                values["demand_enabled"] = "1" if Config.DEMAND_ENABLED else "0"
            return jsonify({
                "settings": values,
                "features": {
                    "env": {
                        "aliso_ui": Config.ALISO_UI,
                        "stream_search": Config.STREAM_SEARCH,
                        "admin_v2": Config.ADMIN_V2,
                        "demand_master": Config.DEMAND_ENABLED,
                        "notice_master": Config.SITE_NOTICE_ENABLED,
                    },
                    "effective": site_config.public_settings()["features"],
                },
            })
        except Exception as e:
            logger.error("读取后台设置失败: %s", e)
            return jsonify({"error": "读取设置失败"}), 500

    @admin_bp.route("/admin/settings", methods=["PUT"])
    @require_admin
    def settings_put():
        body = request.get_json(silent=True) or {}
        raw = body.get("settings")
        if not isinstance(raw, dict):
            return jsonify({"error": "请提供 settings 对象"}), 400

        clean, errors = {}, {}
        for key in SETTINGS_KEYS:
            if key not in raw:
                continue
            value, error = _validate_setting(key, raw[key])
            if error:
                errors[key] = error
            else:
                clean[key] = value
        if errors:
            return jsonify({"error": "设置校验失败", "fields": errors}), 400
        if not clean:
            return jsonify({"error": "没有可保存的设置"}), 400

        try:
            for key, value in clean.items():
                site_config.set_setting(key, value)
            admin_store.audit(_user_name(), "settings_update",
                              ",".join(clean.keys()))
            return jsonify({
                "ok": True,
                "settings": {k: site_config.get_settings().get(k, "")
                             for k in SETTINGS_KEYS},
                "public": site_config.public_settings(),
            })
        except Exception as e:
            logger.error("保存后台设置失败: %s", e)
            return jsonify({"error": "保存设置失败"}), 500

    app.register_blueprint(admin_bp, url_prefix="/api")
