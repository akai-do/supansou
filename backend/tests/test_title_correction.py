"""
中文片名近似纠错回归测试。

运行：
  PYTHONPATH=backend python -m unittest backend.tests.test_title_correction
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from search_engine import title_correct as tc  # noqa: E402


class CleanCandidateTitleTest(unittest.TestCase):
    def test_strips_year_quality_and_packaging(self):
        raw = ("喜羊羊与灰太狼之破界山海诀\u200e (2026) 4K SDR 高码率 "
               "中字【1.4GB/集】全60集")
        self.assertEqual(tc.clean_candidate_title(raw),
                         "喜羊羊与灰太狼之破界山海诀")

    def test_strips_emoji_labels_and_season(self):
        raw = ("🎬 喜羊羊与灰太狼之羊羊欢喜过佳节 (2020) "
               "[喜羊羊与灰太狼]剧版[全530集]")
        self.assertEqual(tc.clean_candidate_title(raw),
                         "喜羊羊与灰太狼之羊羊欢喜过佳节")

    def test_strips_collection_suffix(self):
        self.assertEqual(tc.clean_candidate_title("喜羊羊与灰太狼 全系列 含大电影"),
                         "喜羊羊与灰太狼")

    def test_keeps_book_title_brackets(self):
        # 《》是作品名本身的一部分，不能当包装删掉
        self.assertTrue(tc.clean_candidate_title("《来自深渊》三部曲合集")
                        .startswith("《来自深渊》"))


class NearMissTest(unittest.TestCase):
    def test_homophone_typo_is_near_miss(self):
        self.assertTrue(tc.is_near_miss(
            "喜洋洋与灰太狼之破界山海诀",
            "喜羊羊与灰太狼之破界山海诀"))

    def test_same_series_different_movie_is_not(self):
        self.assertFalse(tc.is_near_miss(
            "喜洋洋与灰太狼之破界山海诀",
            "喜羊羊与灰太狼之虎虎生威"))

    def test_unrelated_title_is_not(self):
        self.assertFalse(tc.is_near_miss(
            "光年探索号", "变形金刚 系列八部合集4K蓝光原盘"))

    def test_short_query_is_skipped(self):
        self.assertFalse(tc.is_near_miss("三体", "三体电视剧"))

    def test_exact_title_is_not_a_correction(self):
        self.assertFalse(tc.is_near_miss(
            "喜羊羊与灰太狼之破界山海诀",
            "喜羊羊与灰太狼之破界山海诀‎ (2026) 4K"))

    def test_pick_prefers_exact_over_spinoff(self):
        candidates = [
            "喜羊羊与灰太狼之破界山海诀\u200e (2026) 4K 全60集",
            "喜羊羊与灰太狼之破界山海诀 番外篇",
            "喜羊羊与灰太狼之虎虎生威",
        ]
        picked = tc.pick_near_miss("喜洋洋与灰太狼之破界山海诀", candidates)
        self.assertEqual(picked[0], "喜羊羊与灰太狼之破界山海诀")


class SegmentTest(unittest.TestCase):
    def test_long_title_uses_distinctive_tail(self):
        segs = tc.cjk_segments("喜洋洋与灰太狼之破界山海诀")
        self.assertEqual(segs[0], "破界山海诀")
        self.assertIn("灰太狼", segs)

    def test_generic_segments_filtered(self):
        self.assertEqual(tc.cjk_segments("电影与电视剧"), [])

    def test_short_title_segment_is_kept(self):
        self.assertEqual(tc.cjk_segments("光年探索号")[0], "光年探索号")


class _FakeIndex:
    def __init__(self, rows):
        self.rows = rows

    def search(self, keyword, **kwargs):
        return list(self.rows)


class _FakeDouban:
    enabled = True

    def __init__(self, titles=None):
        self.titles = titles or []

    def cooldown_active(self):
        return False

    def suggest_titles(self, query):
        return [{"title": t} for t in self.titles]


class _FakePansou:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def search(self, keyword, **kwargs):
        self.calls.append(keyword)
        results = []
        for i, title in enumerate(self.mapping.get(keyword, [])):
            results.append({
                "title": title,
                "links": [{"type": "quark", "url": f"https://pan.quark.cn/s/{i}"}],
            })
        return {"data": {"results": results}}


class CorrectorTest(unittest.TestCase):
    def test_uses_pansou_segment_when_typo_search_dies(self):
        pansou = _FakePansou({
            "破界山海诀": [
                "喜羊羊与灰太狼之破界山海诀\u200e (2026) 4K 全60集",
            ],
        })
        corrector = tc.TitleCorrector()
        out = corrector.suggest(
            "喜洋洋与灰太狼之破界山海诀",
            pansou_client=pansou,
            index_db=_FakeIndex([]),
            douban_client=_FakeDouban(),
        )
        self.assertEqual(out[0], "喜羊羊与灰太狼之破界山海诀")
        self.assertIn("破界山海诀", pansou.calls)

    def test_uses_local_index_segment_before_pansou(self):
        class _SegmentIndex:
            def search(self, keyword, **kwargs):
                if keyword == "破界山海诀":
                    return [{"title": "喜羊羊与灰太狼之破界山海诀 (2026)"}]
                return []

        pansou = _FakePansou({})
        corrector = tc.TitleCorrector()
        out = corrector.suggest(
            "喜洋洋与灰太狼之破界山海诀",
            pansou_client=pansou,
            index_db=_SegmentIndex(),
            douban_client=_FakeDouban(),
        )
        self.assertEqual(out[0], "喜羊羊与灰太狼之破界山海诀")
        # 本地索引已经命中，不应该再等 PanSou 的十秒网络搜索
        self.assertEqual(pansou.calls, [])

    def test_no_false_correction_for_unrelated_title(self):
        pansou = _FakePansou({
            "破界山海诀": ["周星驰 系列电影合集", "变形金刚 系列八部合集"],
        })
        corrector = tc.TitleCorrector()
        out = corrector.suggest(
            "光年探索号之破界山海诀",
            pansou_client=pansou,
            index_db=_FakeIndex([]),
            douban_client=_FakeDouban(),
        )
        self.assertEqual(out, [])

    def test_single_segment_equal_to_query_is_not_researched(self):
        # 原词刚刚已经零结果，再拿同一个词搜一遍只是浪费 10 秒
        pansou = _FakePansou({"光年探索号": ["变形金刚 系列八部合集"]})
        corrector = tc.TitleCorrector()
        out = corrector.suggest(
            "光年探索号",
            pansou_client=pansou,
            index_db=_FakeIndex([]),
            douban_client=_FakeDouban(),
        )
        self.assertEqual(out, [])
        self.assertEqual(pansou.calls, [])

    def test_douban_candidate_is_used_before_pansou(self):
        pansou = _FakePansou({})
        corrector = tc.TitleCorrector()
        out = corrector.suggest(
            "蜘蛛侠英雄无归",
            pansou_client=pansou,
            index_db=_FakeIndex([]),
            douban_client=_FakeDouban(["蜘蛛侠：英雄无归"]),
        )
        # 只差标点：归一化后是同一个词，不需要纠错
        self.assertEqual(out, [])
        self.assertEqual(pansou.calls, [])

    def test_deadline_skips_pansou(self):
        pansou = _FakePansou({})
        corrector = tc.TitleCorrector()
        out = corrector.suggest(
            "喜洋洋与灰太狼之破界山海诀",
            pansou_client=pansou,
            index_db=_FakeIndex([]),
            deadline=time.time() - 1,
        )
        self.assertEqual(out, [])
        self.assertEqual(pansou.calls, [])

    def test_result_is_cached(self):
        pansou = _FakePansou({
            "破界山海诀": ["喜羊羊与灰太狼之破界山海诀 (2026)"],
        })
        corrector = tc.TitleCorrector()
        args = dict(pansou_client=pansou, index_db=_FakeIndex([]),
                    douban_client=_FakeDouban())
        first = corrector.suggest("喜洋洋与灰太狼之破界山海诀", **args)
        calls_after_first = len(pansou.calls)
        second = corrector.suggest("喜洋洋与灰太狼之破界山海诀", **args)
        self.assertEqual(first, second)
        self.assertEqual(len(pansou.calls), calls_after_first)


if __name__ == "__main__":
    unittest.main(verbosity=2)
