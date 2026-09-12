"""
DuPanSou-Archive 应用配置
"""
import os

class Config:
    # 服务端口
    PORT = int(os.getenv("PORT", "5000"))

    # ==========================================
    # PanSou 后端配置 —— 你的项目依赖它做实时搜索
    # 你可以：
    #   1. 本地启动 PanSou Go 服务（推荐，默认 http://localhost:8888）
    #   2. 使用远程部署的 PanSou 实例
    # ==========================================
    PANSOU_BASE_URL = os.getenv("PANSOU_BASE_URL", "http://localhost:8888")
    PANSOU_TIMEOUT = int(os.getenv("PANSOU_TIMEOUT", "15"))  # PanSou 请求超时（秒）

    # PanSou 认证（可选）：若 PanSou 启用了 AUTH_ENABLED，需提供用户名/密码自动登录
    PANSOU_USERNAME = os.getenv("PANSOU_USERNAME", "")
    PANSOU_PASSWORD = os.getenv("PANSOU_PASSWORD", "")

    # ==========================================
    # 索引数据库（SQLite）配置
    # ==========================================
    INDEX_DB_PATH = os.getenv("INDEX_DB_PATH", os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "data", "index.db"
    ))

    # ==========================================
    # 定时收割器配置
    # ==========================================
    HARVEST_KEYWORDS = os.getenv(
        "HARVEST_KEYWORDS",
        "电影,电视剧,动漫,教程,课程,考研,学习资料,软件,电子书,小说,音乐,设计,编程,英语"
    ).split(",")
    HARVEST_INTERVAL_HOURS = int(os.getenv("HARVEST_INTERVAL_HOURS", "6"))

    # ==========================================
    # 链接巡检配置
    # ==========================================
    CHECKER_INTERVAL_HOURS = int(os.getenv("CHECKER_INTERVAL_HOURS", "24"))
    CHECKER_BATCH_SIZE = int(os.getenv("CHECKER_BATCH_SIZE", "20"))

    # ==========================================
    # 搜索配置
    # ==========================================
    DEFAULT_CONCURRENCY = int(os.getenv("DEFAULT_CONCURRENCY", "10"))
    CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "300"))  # 搜索缓存5分钟

    # ==========================================
    # 召回扩源配置
    # ==========================================
    # PanSou 是异步爬取：首搜只返回部分结果，后台继续爬、缓存随之增长。
    # 实时链接数低于阈值时，等待后重查同一批查询词，最多 RECALL_REQUERY_MAX 次。
    RECALL_REQUERY_THRESHOLD = int(os.getenv("RECALL_REQUERY_THRESHOLD", "60"))
    RECALL_REQUERY_MAX = int(os.getenv("RECALL_REQUERY_MAX", "1"))
    RECALL_REQUERY_WAIT = float(os.getenv("RECALL_REQUERY_WAIT", "2"))

    # 补充变体修饰词：对主关键词追加这些词扩搜（结果仍须经原词相关度过滤）
    SUPPLEMENT_MODIFIERS_CJK = os.getenv("SUPPLEMENT_MODIFIERS_CJK", "合集,资源,网盘").split(",")
    SUPPLEMENT_MODIFIERS_ASCII = os.getenv("SUPPLEMENT_MODIFIERS_ASCII", "教程,合集,电子书").split(",")