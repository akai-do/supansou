"""公开只读接口：站点配置、公告、首页热门/历史/本地词条。

所有接口都不需要登录，且只返回可公开的数据：
  * 不返回 token / 账号 / Cookie；
  * 搜索历史只返回全站聚合词，不返回任何个人身份或原始访客记录。
"""
import logging
import re

from flask import Blueprint, jsonify, request

import site_config

logger = logging.getLogger("api.public")

public_bp = Blueprint("public", __name__)


# 首页是公开门面：索引里偶发混入的成人/招嫖类标题不应上屏。
_DENY_WORDS = (
    "人妻", "约炮", "约会指南", "成人", "色情", "裸聊", "性爱", "福利姬",
    "嫖", "淫", "一夜情", "援交",
)


def _clean_word(text: str) -> str:
    """首页词条降噪：过滤 URL、@、超长广告标题与明显不合适的内容。"""
    t = (text or "").strip().replace("\n", " ")
    if not t or len(t) > 28:
        return ""
    if re.search(r"https?:|www\.|t\.me/|@", t, re.IGNORECASE):
        return ""
    low = t.lower()
    if any(w in low for w in _DENY_WORDS):
        return ""
    return t


def register_public_routes(app, index_db) -> None:
    """由 api.register_routes 调用，注入索引库实例。"""
    site_config.ensure_schema()
    if "public" in app.blueprints:
        return

    @public_bp.route("/public/settings", methods=["GET"])
    def public_settings():
        try:
            return jsonify(site_config.public_settings())
        except Exception as e:
            logger.error("读取站点配置失败: %s", e)
            return jsonify({"error": "站点配置读取失败"}), 500

    @public_bp.route("/notice", methods=["GET"])
    def notice():
        try:
            return jsonify(site_config.get_notice())
        except Exception as e:
            logger.error("读取公告失败: %s", e)
            return jsonify({"error": "公告读取失败"}), 500

    @public_bp.route("/hot-search-words", methods=["GET"])
    def hot_search_words():
        try:
            limit = min(30, max(1, int(request.args.get("limit", "18"))))
        except (TypeError, ValueError):
            limit = 18
        try:
            stats = index_db.get_stats()
            raw = stats.get("hot_keywords") or []
            words, seen = [], set()
            for item in raw:
                text = _clean_word(item.get("title") or "")
                if not text or text in seen:
                    continue
                seen.add(text)
                words.append({"text": text, "count": item.get("count") or 0})
                if len(words) >= limit:
                    break
            return jsonify({"words": words, "total": stats.get("total", 0)})
        except Exception as e:
            logger.error("读取热门词失败: %s", e)
            return jsonify({"words": [], "total": 0, "error": "热门词读取失败"})

    @public_bp.route("/search-history", methods=["GET"])
    def search_history():
        """首页“历史”的兼容接口。

        隐私边界：服务端**不公开**任何访客的原始搜索记录。
        首页“历史”行读取浏览器 localStorage 的 dps-search-history；
        这里只保留接口形状，避免把 search_history 表变成公开数据源。
        """
        return jsonify({
            "history": [],
            "scope": "local-only",
            "note": "首页历史仅保存在本机浏览器；服务端不公开他人搜索记录。",
        })

    @public_bp.route("/resource-titles", methods=["GET"])
    def resource_titles():
        try:
            limit = min(60, max(1, int(request.args.get("limit", "24"))))
        except (TypeError, ValueError):
            limit = 24
        # random=1：随机抽样（首页“本地”行）。缺省仍按最近入库，兼容其它调用方。
        randomize = (request.args.get("random") or "").lower() in ("1", "true", "yes")
        try:
            raw = (index_db.get_random_titles(limit * 4) if randomize
                   else index_db.get_recent_titles(limit * 4))
            titles, seen = [], set()
            for item in raw:
                text = _clean_word(item)
                if not text or text in seen:
                    continue
                seen.add(text)
                titles.append(text)
                if len(titles) >= limit:
                    break
            return jsonify({"titles": titles})
        except Exception as e:
            logger.error("读取本地词条失败: %s", e)
            return jsonify({"titles": [], "error": "本地词条读取失败"})

    app.register_blueprint(public_bp, url_prefix="/api")
