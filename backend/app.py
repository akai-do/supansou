"""
DuPanSou-Archive 主入口

启动方式:
  python app.py

启动前需要先确保 PanSou Go 服务在运行（http://localhost:8888），
或者在环境变量中指定 PANSOU_BASE_URL。

完整启动命令:
  # 终端1: 启动 PanSou 搜索引擎
  cd pansou && ./pansou

  # 终端2: 启动 DuPanSou-Archive
  cd DuPanSou-Archive/backend
  pip install -r requirements.txt
  python app.py
"""
import os
import sys
import logging
import mimetypes
from flask import Flask, send_from_directory

# Windows 注册表可能把 .js/.css 映射成 text/plain，导致浏览器以严格 MIME
# 校验拒绝执行 type="module" 脚本（页面白屏只有背景）。这里强制纠正。
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/json", ".json")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")

# 将项目根目录加入路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import Config
from search_engine import PansouClient, extract_links_from_result as extract_links
from indexer import IndexDatabase
from indexer.harvester import Harvester
from checker import LinkChecker
from analyzer import Analyzer
from douban import DoubanClient


# ==========================================
# 日志配置
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("app")


# ==========================================
# 初始化各模块
# ==========================================

# 1. PanSou 搜索引擎客户端
pansou_client = PansouClient(
    base_url=Config.PANSOU_BASE_URL,
    timeout=Config.PANSOU_TIMEOUT,
)

# 2. 索引数据库
index_db = IndexDatabase(db_path=Config.INDEX_DB_PATH)

# 3. 搜索数据提取器（注入潘多拉客户端）
class Searcher:
    """搜索器，封装 PanSou 调用和收割器"""

    def __init__(self, pansou_client, index_db):
        self.pansou_client = pansou_client
        self.index_db = index_db
        self.harvester = None

    def extract_links(self, response: dict) -> list:
        """从 PanSou 搜索结果中提取链接列表"""
        return extract_links(response)

    def setup_harvester(self):
        """设置并启动收割器"""
        def harvest_search(keyword: str) -> list:
            """收割用的搜索函数：PanSou raw 全量 + 学霸盘书源，双源合并"""
            resp = self.pansou_client.search(keyword, result_type="all")
            links = self.extract_links(resp)
            try:
                from search_engine.xuebapan import xuebapan_client
                links = links + xuebapan_client.search(keyword)
            except Exception as e:
                logger.warning(f"学霸盘收割失败（忽略）: {e}")
            return links

        self.harvester = Harvester(
            index_db=self.index_db,
            search_func=harvest_search,
            keywords=Config.HARVEST_KEYWORDS,
            interval_hours=Config.HARVEST_INTERVAL_HOURS,
        )
        return self.harvester

searcher = Searcher(pansou_client, index_db)

# 4. 链接巡检器
checker = LinkChecker(
    index_db=index_db,
    check_func=lambda items: _check_links(items),
    interval_hours=Config.CHECKER_INTERVAL_HOURS,
)


def _check_links(items: list) -> list:
    """
    调用 PanSou 的链接检测 API，返回原始状态供智能检测状态机判读。
    state: ok(有效) / bad(失败) / uncertain(需验证) / unsupported(不支持检测)
    """
    check_items = [
        {"disk_type": dt, "url": url, "password": pw}
        for url, dt, pw in items
    ]
    resp = pansou_client.check_links(check_items)
    return [
        {
            "url": r.get("url", ""),
            "state": r.get("state", "uncertain"),
            "summary": r.get("summary", ""),
        }
        for r in (resp.get("results") or [])
    ]


# 5. 分析器
analyzer = Analyzer(index_db=index_db)

# 6. 豆瓣客户端（榜单 + 资源封面）
douban_client = DoubanClient()


# ==========================================
# 创建 Flask 应用
# ==========================================
def create_app():
    app = Flask(__name__, static_folder=None)

    # ⚠ 反向代理下必须修正客户端 IP：nginx 反代时 request.remote_addr 会变成
    # 127.0.0.1，而访客判定（access.is_station）把 loopback 当站长 —— 不修的话
    # **所有公网访客都会被当成站长**，解析口令与每 IP 配额统统失效。
    # x_for=1 只信任 nginx 追加的那一段（werkzeug 取最右侧）。
    #
    # 但"信任 XFF"本身有前提：**只有请求确实来自我们自己的基础设施时才可信**。
    # 所以先用一层极薄中间件把**未修正前的直连方**记到 dps.raw_peer，
    # 由 access.is_station 判断：直连方是内网/回环 → 采用 XFF 还原的客户端 IP；
    # 直连方是公网（说明有人绕过 nginx 直连后端）→ 一律不认转发头。
    if os.getenv("TRUST_PROXY", "1") == "1":
        from werkzeug.middleware.proxy_fix import ProxyFix

        class _RawPeer:
            """记下 ProxyFix 改写之前的直连方地址。"""

            def __init__(self, app_):
                self.app = app_

            def __call__(self, environ, start_response):
                environ["dps.raw_peer"] = environ.get("REMOTE_ADDR", "")
                return self.app(environ, start_response)

        app.wsgi_app = _RawPeer(ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1))

    # 注册 API 路由
    from api import register_routes
    register_routes(app, searcher, index_db, checker, analyzer,
                    pansou_client, douban_client)

    # 跨域支持：让 https://xiaowusu.com 上的提速页能请求**本机**后端
    # （加速只能在本机执行，云端页面需要反向调用 127.0.0.1，属于跨域）
    from cors import register_cors
    register_cors(app)

    # 注册网盘加速模块（独立蓝图 /api/accel：BDUSS + 解析 + aria2 下载）
    from accel import register_accel
    register_accel(app)

    # 前端静态文件服务
    frontend_dist = os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "frontend", "dist"
    ))
    if os.path.isdir(frontend_dist):
        @app.route("/", defaults={"path": ""})
        @app.route("/<path:path>")
        def serve_frontend(path):
            if path and os.path.isfile(os.path.join(frontend_dist, path)):
                return send_from_directory(frontend_dist, path)
            # index.html 强制不缓存：前端资源带 hash 文件名可长缓存，
            # 但入口页必须每次拿最新，否则发版后浏览器残留旧 JS 引发诡异问题
            resp = send_from_directory(frontend_dist, "index.html")
            resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            return resp
    else:
        # 降级页：仓库自带 dist，正常克隆不会走到这里；
        # 只有自行删除/重建 dist 失败时才出现，给出明确指引而非白屏
        @app.route("/", defaults={"path": ""})
        @app.route("/<path:path>")
        def serve_frontend_missing(path):
            return (
                "<h1>DuPanSou-Archive</h1>"
                "<p>前端页面缺失（frontend/dist 不存在）。后端 API 仍可用：</p>"
                "<ul><li><code>POST /api/search</code> 等接口正常</li></ul>"
                "<p>恢复前端：</p>"
                "<pre>cd frontend\nnpm install\nnpm run build</pre>"
            ), 503

    return app


app = create_app()


# ==========================================
# 启动入口
# ==========================================
if __name__ == "__main__":
    # 安全默认：仅监听本机；局域网/Docker 部署请显式设置 HOST=0.0.0.0
    host = os.getenv("HOST", "127.0.0.1")
    # Werkzeug 调试器可在交互页执行任意代码，严禁对外网开放，默认关闭
    debug = os.getenv("DEBUG", "0") == "1"

    logger.info("=" * 50)
    logger.info("  DuPanSou-Archive 启动中...")
    logger.info(f"  监听: http://{host}:{Config.PORT}  (debug={debug})")
    logger.info(f"  PanSou 后端: {Config.PANSOU_BASE_URL}")
    logger.info(f"  索引数据库: {Config.INDEX_DB_PATH}")
    logger.info("=" * 50)

    # 检查 PanSou 是否在线
    health = pansou_client.check_health()
    if health.get("status") == "offline":
        logger.warning(f"⚠  PanSou 服务未运行在 {Config.PANSOU_BASE_URL}")
        logger.warning(f"   搜索功能将降级为仅历史索引模式")
        logger.warning(f"   一键启动: docker run -d --name pansou -p 8888:8888 "
                       f"-e PORT=8888 -e AUTH_ENABLED=false ghcr.io/fish2018/pansou:latest")
    else:
        logger.info(f"✅ PanSou 服务在线")

    # 启动收割器 / 巡检器（后台线程）
    # ACCEL_NO_BACKGROUND=1 时全部不启动 —— 给"只当下载提速器用"的分发包，
    # 避免在别人机器上白跑采集与巡检（占带宽、写索引库）。
    if os.getenv("ACCEL_NO_BACKGROUND") == "1":
        logger.info("  [后台任务] 已按 ACCEL_NO_BACKGROUND=1 关闭采集与巡检")
    else:
        harvester = searcher.setup_harvester()
        harvester.start()
        checker.start()

    # 打印初始索引统计
    stats = index_db.get_stats()
    logger.info(f"  索引库: {stats['total']} 条链接, "
                f"存活 {stats['alive']}, 失效 {stats['dead']}")

    # 启动 Flask 服务
    app.run(
        host=host,
        port=Config.PORT,
        debug=debug,
        use_reloader=False,  # 关闭自动重载（否则后台线程会重复启动）
    )

    # 停止后台服务
    harvester.stop()
    checker.stop()
    index_db.close()