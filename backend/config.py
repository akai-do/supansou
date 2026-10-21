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
    # 来源质量统计（曝光/点击/死链率）
    # ==========================================
    # 搜索结果出页时按“来源 + 天 + 关键词”累加曝光；关闭后只保留死链率
    SOURCE_STATS_ENABLED = os.getenv("SOURCE_STATS_ENABLED", "1") == "1"
    # 来源质量表缓存秒数（62k 行聚合不便宜，避免每次打开统计页都重算）
    SOURCE_STATS_CACHE_SECONDS = int(os.getenv("SOURCE_STATS_CACHE_SECONDS", "60"))
    # 少于该样本量时只显示“样本不足”，不参与排名/建议
    SOURCE_LOW_SAMPLE_THRESHOLD = int(os.getenv("SOURCE_LOW_SAMPLE_THRESHOLD", "20"))
    # 来源质量降权：只对 checked>=阈值、死链率>=50%、独家率<20% 的来源生效
    SOURCE_QUALITY_RANKING = os.getenv("SOURCE_QUALITY_RANKING", "1") == "1"
    # 第一页单来源最多占几张卡；0 表示不做多样性限制。
    # 8 张会让一个来源刷满 top10，实测降到 4 更均衡。
    SOURCE_DIVERSITY_CAP = int(os.getenv("SOURCE_DIVERSITY_CAP", "4"))
    # 第一页同一标题簇（同资源不同发布帖）最多占几张卡；0 表示关闭。
    # 标题键保留年份/季集：不同年份/不同季集视为不同资源，不强行拆散。
    TITLE_CLUSTER_CAP = int(os.getenv("TITLE_CLUSTER_CAP", "3"))

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
    # "无匹配"封面的缓存时长（过期后重试；豆瓣限流期的空结果不会永久粘连）
    POSTER_EMPTY_TTL_HOURS = int(os.getenv("POSTER_EMPTY_TTL_HOURS", "24"))

    # ==========================================
    # aliso 1:1 改造开关（代码内默认值，部署时可用环境变量逐项覆盖）
    # ==========================================
    ALISO_UI = os.getenv("ALISO_UI", "1") == "1"
    # 流式搜索：本地索引先行下发，实时首批/补充召回渐进推送（前端自动回退经典接口）
    STREAM_SEARCH = os.getenv("STREAM_SEARCH", "1") == "1"
    # 流式轮询间隔：实时波次内每隔多久检查线程完成度，链接数增长即推送中间帧
    STREAM_POLL_SECONDS = float(os.getenv("STREAM_POLL_SECONDS", "0.8"))
    # 单次流式搜索硬性时长上限（秒），超过按错误帧收尾（纠错重搜也在预算内）
    STREAM_MAX_SECONDS = int(os.getenv("STREAM_MAX_SECONDS", "90"))
    DEMAND_ENABLED = os.getenv("DEMAND_ENABLED", "0") == "1"
    ADMIN_V2 = os.getenv("ADMIN_V2", "0") == "1"
    SITE_NOTICE_ENABLED = os.getenv("SITE_NOTICE_ENABLED", "1") == "1"

    # 站点公开信息默认值（后续可在 site_settings 表里修改）
    SITE_NAME = os.getenv("SITE_NAME", "SuPanSou")
    SITE_SUBTITLE = os.getenv("SITE_SUBTITLE", "网盘资源聚合搜索")
    SITE_LOGO = os.getenv("SITE_LOGO", "/favicon.svg")
    SITE_NOTICE_TEXT = os.getenv(
        "SITE_NOTICE_TEXT",
        "本站仅提供网盘资源索引与链接有效性检测，不存储任何文件；请勿用于商业用途。",
    )
    SITE_NOTICE_VERSION = os.getenv("SITE_NOTICE_VERSION", "2026-10-19")

    # ==========================================
    # 管理后台（与 ACCEL_TOKEN 完全分离）
    # ==========================================
    # 首次启动时用这两个环境变量创建默认管理员；密码只存哈希，不写日志。
    # ADMIN_PASSWORD 为空时不创建默认账号，登录接口会提示“后台未初始化”。
    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
    # 会话签名密钥；为空时进程启动随机生成（重启后需重新登录）
    ADMIN_SECRET_KEY = os.getenv("ADMIN_SECRET_KEY", "")
    ADMIN_SESSION_HOURS = int(os.getenv("ADMIN_SESSION_HOURS", "12"))
    ADMIN_COOKIE_SECURE = os.getenv("ADMIN_COOKIE_SECURE", "0") == "1"

    # ==========================================
    # 需求板防刷（内存计数，重启清零）
    # ==========================================
    DEMAND_SUBMIT_LIMIT = int(os.getenv("DEMAND_SUBMIT_LIMIT", "3"))
    DEMAND_SUBMIT_WINDOW_MINUTES = int(os.getenv("DEMAND_SUBMIT_WINDOW_MINUTES", "10"))
    DEMAND_VOTE_LIMIT = int(os.getenv("DEMAND_VOTE_LIMIT", "30"))
    DEMAND_VOTE_WINDOW_MINUTES = int(os.getenv("DEMAND_VOTE_WINDOW_MINUTES", "1"))
    DEMAND_COMMENT_LIMIT = int(os.getenv("DEMAND_COMMENT_LIMIT", "8"))
    DEMAND_COMMENT_WINDOW_MINUTES = int(os.getenv(
        "DEMAND_COMMENT_WINDOW_MINUTES", "5"))