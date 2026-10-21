"""
source_stats —— 来源质量统计的共享工具

只做纯函数，不依赖 Flask / SQLite，方便 indexer、analyzer 和离线脚本复用：
  * classify_category(text)  查询/标题 → 影视/学习/软件/音乐/其他
  * normalize_title(title)   标题归一化（用于计算来源独家率）
  * today_str()              本地日期（曝光/点击按天聚合）
"""
import re
from datetime import datetime

CATEGORY_LABELS = {
    "all": "全部",
    "movie": "影视",
    "study": "学习",
    "software": "软件",
    "music": "音乐",
    "other": "其他",
}

# 关键词按类别归类；命中数最多的类别胜出，平票时按 _PRIORITY 决定。
# 分类只用于统计维度，不参与搜索排序，允许启发式误差。
_CATEGORY_KEYWORDS = {
    "movie": (
        "电影", "电视剧", "动漫", "动画", "综艺", "纪录片", "剧集", "短剧",
        "影院", "导演", "演员", "票房", "美剧", "韩剧", "日剧", "港剧",
        "国漫", "番剧", "中字", "字幕", "4k", "8k", "蓝光", "bluray",
        "remux", "webdl", "web-dl", "hdr", "杜比", "原盘",
    ),
    "study": (
        "考研", "教材", "真题", "课程", "网课", "教程", "学习", "小学",
        "初中", "高中", "大学", "英语", "数学", "语文", "物理", "化学",
        "政治", "历史", "地理", "生物", "编程", "python", "java", "c++",
        "前端", "后端", "算法", "数据结构", "四六级", "雅思", "托福",
        "教资", "法考", "会计", "cpa", "课件", "讲义", "试卷", "习题",
        "答案", "电子书", "阅读", "绘本", "奥数", "作文", "单词", "语法",
        "笔记", "题库", "考公", "公务员", "事业单位", "事业编", "医学",
        "护士", "药师", "建造师", "消防", "网课", "名师", "复习",
    ),
    "software": (
        "软件", "工具", "破解", "安装包", "插件", "ps", "pr", "ae",
        "office", "windows", "mac", "安卓", "app", "源码", "绿色版",
        "便携版", "激活", "汉化", "驱动", "系统", "photoshop",
        "premiere", "excel", "word", "ppt", "wps", "cad", "matlab",
    ),
    "music": (
        "音乐", "歌曲", "专辑", "无损", "flac", "mp3", "wav", "演唱会",
        "ost", "原声", "歌单", "dj", "hires", "母带",
    ),
}

# 平票优先级：软件工具优先于学习（如“PS教程”归软件），学习优先于影视。
_PRIORITY = ("software", "study", "movie", "music")


def classify_category(text: str) -> str:
    """把查询或标题归到 movie/study/software/music/other。"""
    t = (text or "").lower()
    if not t:
        return "other"
    scores = {}
    for cat, words in _CATEGORY_KEYWORDS.items():
        hits = sum(1 for w in words if w in t)
        if hits:
            scores[cat] = hits
    if not scores:
        return "other"
    best = max(scores.values())
    for cat in _PRIORITY:
        if scores.get(cat) == best:
            return cat
    return max(scores, key=scores.get)


def normalize_title(title: str) -> str:
    """标题归一化：小写、去空白/标点，用于同资源跨来源比较。"""
    t = (title or "").lower()
    t = re.sub(
        r"[\s\-_·|:：;；,，.。?？!！~～\"'“”‘’()（）\[\]【】{}<>《》「」『』]+",
        "",
        t,
    )
    return t[:120]


def today_str() -> str:
    """本地日期（与容器 TZ 一致），曝光/点击按天聚合。"""
    return datetime.now().strftime("%Y-%m-%d")
