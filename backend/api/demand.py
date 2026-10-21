"""需求板公开接口：提交、历史列表、想要投票。"""
import logging

from flask import Blueprint, jsonify, request

import demand_store
import site_config
from config import Config

logger = logging.getLogger("api.demand")
demand_bp = Blueprint("demand", __name__)


def register_demand_routes(app) -> None:
    demand_store.ensure_schema()
    if "demand" in app.blueprints:
        return

    def _enabled() -> bool:
        return site_config.demand_enabled()

    def _disabled_payload():
        return {
            "items": [],
            "total": 0,
            "page": 1,
            "page_size": 20,
            "total_pages": 1,
            "sort": "want",
            "enabled": False,
        }

    @demand_bp.route("/demands", methods=["GET"])
    def list_demands():
        if not _enabled():
            return jsonify(_disabled_payload())
        try:
            sort = request.args.get("sort", "want")
            page = request.args.get("page", "1")
            page_size = request.args.get("page_size", "20")
            data = demand_store.list_public(
                sort=sort, page=page, page_size=page_size,
                vk=demand_store.voter_key(request),
            )
            data["enabled"] = True
            return jsonify(data)
        except Exception as e:
            logger.error("需求列表读取失败: %s", e)
            return jsonify({"error": "需求列表读取失败", "enabled": True}), 500

    @demand_bp.route("/demand", methods=["POST"])
    def submit_demand():
        if not _enabled():
            return jsonify({"error": "需求板当前未开启"}), 403

        ip = request.remote_addr or ""
        if not demand_store.allow_rate(
            ip, "submit",
            Config.DEMAND_SUBMIT_LIMIT,
            Config.DEMAND_SUBMIT_WINDOW_MINUTES * 60,
        ):
            return jsonify({
                "error": f"提交太频繁，请 "
                         f"{Config.DEMAND_SUBMIT_WINDOW_MINUTES} 分钟后再试"
            }), 429

        body = request.get_json(silent=True) or {}
        clean, error = demand_store.validate_demand(body)
        if error:
            return jsonify({"error": error}), 400
        try:
            demand_id = demand_store.create_demand(clean)
            return jsonify({"ok": True, "id": demand_id}), 201
        except Exception as e:
            logger.error("需求提交失败: %s", e)
            return jsonify({"error": "提交失败，请稍后重试"}), 500

    @demand_bp.route("/demand/<int:demand_id>/comments", methods=["GET"])
    def list_demand_comments(demand_id: int):
        if not _enabled():
            return jsonify({"error": "需求板当前未开启", "items": [], "total": 0}), 403
        try:
            data = demand_store.list_comments(demand_id)
            if data.get("error"):
                return jsonify({"error": data["error"]}), data.get("status", 400)
            return jsonify(data)
        except Exception as e:
            logger.error("评论列表读取失败: %s", e)
            return jsonify({"error": "评论列表读取失败"}), 500

    @demand_bp.route("/demand/<int:demand_id>/comments", methods=["POST"])
    def submit_demand_comment(demand_id: int):
        if not _enabled():
            return jsonify({"error": "需求板当前未开启"}), 403

        ip = request.remote_addr or ""
        if not demand_store.allow_rate(
            ip, "comment",
            Config.DEMAND_COMMENT_LIMIT,
            Config.DEMAND_COMMENT_WINDOW_MINUTES * 60,
        ):
            return jsonify({
                "error": f"评论太频繁，请 "
                         f"{Config.DEMAND_COMMENT_WINDOW_MINUTES} 分钟后再试"
            }), 429

        body = request.get_json(silent=True) or {}
        clean, error = demand_store.validate_comment(body)
        if error:
            return jsonify({"error": error}), 400
        try:
            result = demand_store.create_comment(
                demand_id, clean, demand_store.voter_key(request)
            )
            if result.get("error"):
                return jsonify({"error": result["error"]}), result.get("status", 400)
            return jsonify({"ok": True, **result}), 201
        except Exception as e:
            logger.error("评论提交失败: %s", e)
            return jsonify({"error": "评论提交失败，请稍后重试"}), 500

    @demand_bp.route("/demand/<int:demand_id>/want", methods=["POST"])
    def want_demand(demand_id: int):
        if not _enabled():
            return jsonify({"error": "需求板当前未开启"}), 403

        ip = request.remote_addr or ""
        if not demand_store.allow_rate(
            ip, "vote",
            Config.DEMAND_VOTE_LIMIT,
            Config.DEMAND_VOTE_WINDOW_MINUTES * 60,
        ):
            return jsonify({"error": "操作太频繁，请稍后再试"}), 429

        try:
            result = demand_store.toggle_vote(
                demand_id, demand_store.voter_key(request)
            )
            if result.get("error"):
                return jsonify({"error": result["error"]}), result.get("status", 400)
            return jsonify({"ok": True, **result})
        except Exception as e:
            logger.error("需求投票失败: %s", e)
            return jsonify({"error": "操作失败，请稍后重试"}), 500

    app.register_blueprint(demand_bp, url_prefix="/api")
