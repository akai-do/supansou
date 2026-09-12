"""
spell —— 轻量拼写纠正（Chrome 式"您是不是要找"）

词库来源（纯本地、零外部依赖）：
  1. link_index 标题里的 ASCII 词（len>=3，按词频加权）
  2. search_history 里的历史关键词（权重更高——用户自己的词汇优先纠正）

算法：difflib.get_close_matches（编辑相似度 >= 0.75）。
典型场景：输入 "Xmimd"（Xmind 打错）→ 源级 0 结果 → 纠正为 "Xmind" 重搜。
词库带 TTL 缓存，避免每次搜索全表扫描。
"""
import re
import time
import logging
import threading
from collections import Counter
from difflib import get_close_matches

logger = logging.getLogger("spell")

_ASCII_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+\-#_.]{1,29}")
_VOCAB_TTL = 600          # 词库缓存 10 分钟
_VOCAB_MAX = 30000        # 词库上限
_SIMILARITY_CUTOFF = 0.75


class SpellCorrector:
    def __init__(self):
        self._vocab_counter = Counter()
        self._vocab_list = []
        self._vocab_set = set()
        self._built_at = 0.0
        self._lock = threading.Lock()

    def _ensure_vocab(self, index_db, force=False):
        now = time.time()
        if not force and self._vocab_list and now - self._built_at < _VOCAB_TTL:
            return
        with self._lock:
            if not force and self._vocab_list and now - self._built_at < _VOCAB_TTL:
                return
            counter = Counter()
            try:
                # 历史搜索词：用户自己的词汇，权重最高
                for row in index_db.get_history(limit=100):
                    for w in _ASCII_WORD_RE.findall(row.get("keyword", "")):
                        counter[w.lower()] += 8
                # 索引标题词
                rows = index_db.iter_titles_for_vocab()
                for title in rows:
                    for w in _ASCII_WORD_RE.findall(title or ""):
                        counter[w.lower()] += 1
            except Exception as e:
                logger.error(f"构建纠错词库失败: {e}")
                return
            # 只保留出现 >=2 次的词，截断上限
            vocab = {w: c for w, c in counter.items() if c >= 2}
            if len(vocab) > _VOCAB_MAX:
                vocab = dict(sorted(vocab.items(),
                                    key=lambda kv: -kv[1])[:_VOCAB_MAX])
            self._vocab_counter = Counter(vocab)
            self._vocab_list = list(vocab.keys())
            self._vocab_set = set(self._vocab_list)
            self._built_at = time.time()
            logger.info(f"[纠错] 词库就绪: {len(self._vocab_list)} 词")

    def suggest(self, index_db, keyword: str):
        """
        返回纠正后的完整查询词；无需纠正返回 None。

        只对查询里的 ASCII 词做纠正。候选词必须"显著更常用"才采信：
        频次 >= 5 倍原词频次——防止搜索回声污染（用户搜错词时，
        少数回声标题会把错拼词也写进索引，若只看"是否在词库"就会误判为正确）。
        """
        kw = (keyword or "").strip()
        if not kw or not re.search(r"[a-zA-Z]{3,}", kw):
            return None
        self._ensure_vocab(index_db)
        if not self._vocab_list:
            return None

        tokens = _ASCII_WORD_RE.findall(kw)
        corrected = kw
        changed = False
        for tok in tokens:
            low = tok.lower()
            my_freq = self._vocab_counter.get(low, 0)
            # 词库里没有它，或存在频次显著更高的近邻 → 视为可疑错拼
            matches = [w for w in get_close_matches(low, self._vocab_list,
                                                    n=3, cutoff=_SIMILARITY_CUTOFF)
                       if w != low]
            best = None
            for cand in matches:
                cand_freq = self._vocab_counter.get(cand, 0)
                if cand_freq >= 5 * max(1, my_freq) and cand_freq >= 10:
                    best = cand
                    break
            if best:
                corrected = corrected.replace(tok, best, 1)
                changed = True
        return corrected if changed else None


def suggest_correction(index_db, keyword: str):
    """模块级便捷入口"""
    return _corrector.suggest(index_db, keyword)


_corrector = SpellCorrector()
