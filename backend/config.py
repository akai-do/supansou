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
    CHECKER_INTERVAL_HOURS = int(os.getenv("CHECKER_INTERVAL_HOURS", "1"))
    CHECKER_BATCH_SIZE = int(os.getenv("CHECKER_BATCH_SIZE", "20"))
    CHECKER_CYCLE_LIMIT = int(os.getenv("CHECKER_CYCLE_LIMIT", "300"))  # 每轮最多检测条数

    # ==========================================
    # 智能链接有效性检测（升级自旧版"即时链接体检"）
    # 状态机: ok(有效) / suspect(疑似失效) / dead(确认失效) / ''(未检测)
    # ==========================================
    # 连续失败多少次才确认失效（防检测源抖动误杀；用户举报直接确认）
    DEAD_CONFIRM_STREAK = int(os.getenv("DEAD_CONFIRM_STREAK", "2"))
    # 各状态的新鲜度 TTL：新鲜期内复用结果不重复请求检测源
    VALIDITY_OK_TTL_HOURS = float(os.getenv("VALIDITY_OK_TTL_HOURS", "72"))
    VALIDITY_SUSPECT_TTL_MINUTES = float(os.getenv("VALIDITY_SUSPECT_TTL_MINUTES", "30"))
    VALIDITY_DEAD_TTL_HOURS = float(os.getenv("VALIDITY_DEAD_TTL_HOURS", "12"))
    # 智能检测总开关（关闭后仅剩周期巡检与用户举报）
    SMART_CHECK_ENABLED = os.getenv("SMART_CHECK_ENABLED", "1") == "1"
    # 搜索结果默认隐藏"确认失效"的资源卡（前端可展开查看）
    HIDE_DEAD_LINKS = os.getenv("HIDE_DEAD_LINKS", "1") == "1"
    # 每次新搜索后，后台对前 N 条新鲜度不足的链接静默补检
    SMART_CHECK_BACKGROUND_LIMIT = int(os.getenv("SMART_CHECK_BACKGROUND_LIMIT", "12"))

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

    # ==========================================
    # 书源（学霸盘）与拼写纠错
    # ==========================================
    ENABLE_XUEBAPAN = os.getenv("ENABLE_XUEBAPAN", "true").lower() == "true"
    XUEBAPAN_TIMEOUT = int(os.getenv("XUEBAPAN_TIMEOUT", "8"))
    XUEBAPAN_MAX_ITEMS = int(os.getenv("XUEBAPAN_MAX_ITEMS", "5"))

    # ==========================================
    # 豆瓣榜单与资源封面
    # ==========================================
    ENABLE_DOUBAN = os.getenv("ENABLE_DOUBAN", "1") == "1"
    ENABLE_POSTERS = os.getenv("ENABLE_POSTERS", "1") == "1"
    DOUBAN_TIMEOUT = float(os.getenv("DOUBAN_TIMEOUT", "6"))
    DOUBAN_HOT_TTL_SECONDS = int(os.getenv("DOUBAN_HOT_TTL_SECONDS", "3600"))