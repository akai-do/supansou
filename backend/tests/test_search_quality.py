"""
搜索质量回归测试：相关度过滤、标题归一化、排序与主卡选择。

运行：
  PYTHONPATH=backend python -m unittest backend.tests.test_search_quality
或在容器内：
  docker exec dupansou-backend python -m unittest discover -s /app/tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import api  # noqa: E402


def match(title, keyword, source="test", url=""):
    return api._matches_query(
        {"title": title, "source": source, "url": url},
        keyword, api._tokenize_query(keyword),
    )


class RelevanceFilterTest(unittest.TestCase):
    def test_mixed_query_requires_cjk_intent(self):
        # Office 安装包 不能被只含 office 的电影/办公套件带走
        self.assertTrue(match("Office安装包", "Office 安装包"))
        self.assertTrue(match("Microsoft Office 2024 安装包", "Office 安装包"))
        self.assertFalse(match(
            "同事以上，恋人未满 办公室恋爱 Office Romance (2026) 内封官方简繁",
            "Office 安装包"))
        self.assertFalse(match(
            "The.Office.US.S01-S09.2005-2012", "Office 安装包"))
        self.assertFalse(match("WPS Office 2023（12.82.2155）", "Office 安装包"))
        # 免安装中文版是游戏 repack/绿色版，不是安装包
        self.assertFalse(match(
            "办公室斗争（Office Fight）免安装中文版", "Office 安装包"))
        self.assertTrue(match(
            "Office 2024 专业增强版 全功能绿色精简安装包", "Office 安装包"))

    def test_pure_ascii_query_requires_every_token(self):
        self.assertTrue(match("Windows 11 22H2", "Windows 11"))
        self.assertFalse(match("Windows 10 22H2", "Windows 11"))
        self.assertTrue(match("Photoshop 2024 25.9", "Photoshop 2024"))
        self.assertFalse(match("Photoshop 2023", "Photoshop 2024"))

    def test_short_ascii_word_boundary(self):
        self.assertFalse(match("clips tutorial", "PS"))
        self.assertTrue(match("PS 2024 教程", "PS"))

    def test_long_cjk_query_requires_core(self):
        self.assertTrue(match("高等数学教材合集", "高等数学教材"))
        self.assertTrue(match("高等数学", "高等数学教材"))
        self.assertTrue(match("高等数学资料合集 专升本", "高等数学教材"))
        self.assertFalse(match("小学1-6年级教材全解精析", "高等数学教材"))
        self.assertFalse(match("小学语文数学教材全解", "高等数学教材"))

    def test_strong_intent_suffix(self):
        kw = "2026考研英语一真题解析"
        self.assertTrue(match("考研英语一、二真题解析", kw))
        self.assertTrue(match("1980-2025考研英语真题", kw))
        self.assertFalse(match("2026考研英语词汇", kw))
        self.assertFalse(match("考研英语2027", kw))

    def test_mixed_cjk_can_match_short_intent(self):
        self.assertTrue(match("Python教程", "Python入门教程"))
        self.assertTrue(match("Python编程从入门到实践", "Python入门教程"))
        self.assertFalse(match("Python数据分析", "Python入门教程"))

    def test_short_cjk_requires_prefix(self):
        self.assertTrue(match("活着 (1994)", "活着"))
        self.assertTrue(match("【原盘】活着 (1994)", "活着"))
        # 同前缀但不同作品仍会进过滤器，由排序降权（见 RankingTest）
        self.assertTrue(match("活着就是爱", "活着"))


class TitleNormalizationTest(unittest.TestCase):
    def test_strip_random_suffix(self):
        self.assertEqual(api._strip_random_suffix("电影_8RoUmhV"), "电影")
        self.assertEqual(api._strip_random_suffix("电影-ab12cd"), "电影")
        # 季集/年份/画质不能被当成随机码剥掉
        self.assertEqual(api._strip_random_suffix("电影_S01"), "电影_S01")
        self.assertEqual(api._strip_random_suffix("电影_2024"), "电影_2024")
        # 1080P 是画质角标，按噪声剥掉（分组键里本来也会剔除）
        self.assertEqual(api._strip_random_suffix("电影_1080P"), "电影")

    def test_quality_tokens_merge_same_year_editions(self):
        items = [
            {"title": "让子弹飞 (2010) 1080P 未删减版",
             "url": "u1", "disk_type": "quark", "source": "s"},
            {"title": "让子弹飞 (2010) 已刮销",
             "url": "u2", "disk_type": "quark", "source": "s"},
        ]
        cards = api._group_resources(items)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].get("variant_count"), 1)

    def test_year_compatible_merge(self):
        # 只有一年：无年份标题并入
        cards = api._group_resources([
            {"title": "三体", "url": "a", "disk_type": "quark", "source": "s"},
            {"title": "三体 (2023)", "url": "b", "disk_type": "quark",
             "source": "s"},
        ])
        self.assertEqual(len(cards), 1)
        # 两个不同年份：不合并
        cards = api._group_resources([
            {"title": "三体 (2023)", "url": "a", "disk_type": "quark",
             "source": "s"},
            {"title": "三体 (2024)", "url": "b", "disk_type": "quark",
             "source": "s"},
        ])
        self.assertEqual(len(cards), 2)

    def test_truncated_title_keeps_year_for_grouping(self):
        short = api._shorten_title(
            "肖申克的救赎[国语配音/中文字幕].The.Shawshank.Redemption.1994.1080p")
        self.assertIn("1994", short)
        cards = api._group_resources([
            {"title": short, "url": "a", "disk_type": "quark", "source": "s"},
            {"title": "肖申克的救赎 (1994) 4K REMUX",
             "url": "b", "disk_type": "quark", "source": "s"},
        ])
        self.assertEqual(len(cards), 1)

    def test_english_original_title_merges_for_long_cjk(self):
        cards = api._group_resources([
            {"title": "肖申克的救赎 (1994) 4K REMUX",
             "url": "a", "disk_type": "quark", "source": "s"},
            {"title": "肖申克的救赎.The.Shawshank.Redemption.1994.BluRay",
             "url": "b", "disk_type": "quark", "source": "s"},
        ])
        self.assertEqual(len(cards), 1)

    def test_short_cjk_does_not_drop_latin(self):
        # 活着 VIVANT 是另一部日剧，不能因为去掉英文原名而并进《活着》
        cards = api._group_resources([
            {"title": "活着", "url": "a", "disk_type": "quark", "source": "s"},
            {"title": "活着 VIVANT", "url": "b", "disk_type": "quark",
             "source": "s"},
        ])
        self.assertEqual(len(cards), 2)

    def test_seasons_are_not_merged(self):
        cards = api._group_resources([
            {"title": "庆余年 第一季", "url": "a", "disk_type": "quark",
             "source": "s"},
            {"title": "庆余年 第二季", "url": "b", "disk_type": "quark",
             "source": "s"},
        ])
        self.assertEqual(len(cards), 2)


class RankingTest(unittest.TestCase):
    def test_exact_title_beats_spinoff_prefix(self):
        items = [
            {"title": "活着就是爱", "url": "u1", "disk_type": "quark",
             "source": "s"},
            {"title": "【原盘】活着 (1994)", "url": "u2", "disk_type": "quark",
             "source": "s"},
            {"title": "活着", "url": "u3", "disk_type": "quark", "source": "s"},
        ]
        ranked, _, _ = api._dedup_and_rank([], items, "活着", click_map={})
        titles = [x["title"] for x in ranked]
        self.assertEqual(titles[0], "活着")
        self.assertLess(titles.index("【原盘】活着 (1994)"),
                        titles.index("活着就是爱"))

    def test_installer_beats_game_repack(self):
        items = [
            {"title": "办公室斗争（Office Fight）免安装中文版",
             "url": "u1", "disk_type": "quark", "source": "s"},
            {"title": "Office 2024 专业增强版 安装包",
             "url": "u2", "disk_type": "quark", "source": "s"},
        ]
        ranked, _, _ = api._dedup_and_rank(
            [], items, "Office 安装包", click_map={})
        self.assertEqual(ranked[0]["title"], "Office 2024 专业增强版 安装包")

    def test_different_adaptation_tail_downranked(self):
        items = [
            {"title": "活着(日剧)", "url": "u1", "disk_type": "quark",
             "source": "s"},
            {"title": "活着[美版]", "url": "u2", "disk_type": "quark",
             "source": "s"},
            {"title": "活着", "url": "u3", "disk_type": "quark", "source": "s"},
        ]
        ranked, _, _ = api._dedup_and_rank([], items, "活着", click_map={})
        titles = [x["title"] for x in ranked]
        self.assertEqual(titles[0], "活着")
        self.assertLess(titles.index("活着[美版]"), titles.index("活着(日剧)"))

    def test_software_installer_beats_book(self):
        items = [
            {"title": "《Windows 11操作系统从入门到精通》一本通",
             "url": "u1", "disk_type": "quark", "source": "s"},
            {"title": "Windows 11 22H2 官方VLSC正式版光盘系统 ISO",
             "url": "u2", "disk_type": "quark", "source": "s"},
        ]
        ranked, _, _ = api._dedup_and_rank([], items, "Windows 11", click_map={})
        self.assertEqual(ranked[0]["title"],
                         "Windows 11 22H2 官方VLSC正式版光盘系统 ISO")

    def test_exact_title_beats_longer_title(self):
        items = [
            {"title": "Office 安装包 2024 合集",
             "url": "u1", "disk_type": "quark", "source": "s"},
            {"title": "Office 安装包",
             "url": "u2", "disk_type": "quark", "source": "s"},
        ]
        ranked, _, _ = api._dedup_and_rank(
            [], items, "Office 安装包", click_map={})
        self.assertEqual(ranked[0]["title"], "Office 安装包")


class MainCardTest(unittest.TestCase):
    def test_valid_detail_beats_untested_direct_with_password(self):
        detail = {
            "title": "x", "url": "https://xuebapan.com/info/1",
            "validity": "ok", "disk_type": "baidu", "source": "s",
        }
        direct = {
            "title": "x", "url": "https://pan.quark.cn/s/1",
            "validity": "", "password": "abcd", "disk_type": "quark",
            "source": "s",
        }
        self.assertGreater(api._rep_score(detail), api._rep_score(direct))

    def test_direct_link_beats_detail_when_both_valid(self):
        detail = {
            "title": "x", "url": "https://xuebapan.com/info/1",
            "validity": "ok", "disk_type": "baidu", "source": "s",
        }
        direct = {
            "title": "x", "url": "https://pan.quark.cn/s/1",
            "validity": "ok", "disk_type": "quark", "source": "s",
        }
        self.assertGreater(api._rep_score(direct), api._rep_score(detail))


if __name__ == "__main__":
    unittest.main(verbosity=2)
