"""
去重与高质量排序回归测试。

运行：
  PYTHONPATH=backend python -m unittest backend.tests.test_dedup_ranking
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import api  # noqa: E402


def item(title, url, **kw):
    base = {
        "title": title,
        "url": url,
        "disk_type": kw.pop("disk_type", "quark"),
        "source": kw.pop("source", "test"),
    }
    base.update(kw)
    return base


class EpisodeKeyTest(unittest.TestCase):
    def test_same_season_episodes_share_key(self):
        a = api._normalize_title_key("喜羊羊与灰太狼之破界山海诀 S01E50")
        b = api._normalize_title_key("喜羊羊与灰太狼之破界山海诀 S01E60")
        self.assertEqual(a, b)

    def test_season_one_merges_with_unmarked_pack(self):
        a = api._normalize_title_key("喜羊羊与灰太狼之破界山海诀 全60集")
        b = api._normalize_title_key("喜羊羊与灰太狼之破界山海诀 S01E50")
        self.assertEqual(a, b)

    def test_different_seasons_do_not_merge(self):
        a = api._normalize_title_key("庆余年 S01E05")
        b = api._normalize_title_key("庆余年 S02E05")
        self.assertNotEqual(a, b)

    def test_chinese_season_equals_sxx(self):
        self.assertEqual(
            api._normalize_title_key("庆余年 第二季 第5集"),
            api._normalize_title_key("庆余年 S02"))

    def test_episode_kind(self):
        self.assertEqual(api._episode_kind("剧名 S01E01-E60"), "pack")
        self.assertEqual(api._episode_kind("剧名 S01E05"), "single")
        self.assertEqual(api._episode_kind("剧名 更新至60集"), "pack")
        self.assertEqual(api._episode_kind("剧名 第5集"), "single")
        self.assertEqual(api._episode_kind("剧名 全60集"), "pack")
        self.assertEqual(api._episode_kind("剧名"), "")

    def test_slash_episode_noise_removed(self):
        # “… /集 更22集” 里的 /集 不能残留成 “集”，否则同剧分两组
        self.assertEqual(
            api._normalize_title_key("剧名 (2026) /集 更22集"),
            api._normalize_title_key("剧名 (2026) 更新至60集"))


class EpisodeGroupTest(unittest.TestCase):
    def test_episodes_and_pack_collapse_to_one_card(self):
        cards = api._group_resources([
            item("剧名 S01E01", "u1"),
            item("剧名 S01E02", "u2"),
            item("剧名 全60集 4K", "u3"),
        ])
        self.assertEqual(len(cards), 1)
        card = cards[0]
        self.assertIn("全60集", card["title"])
        self.assertEqual(card["url"], "u3")       # 合集当主卡
        self.assertEqual(card["episode_count"], 2)
        self.assertTrue(card["has_pack"])
        self.assertGreaterEqual(card["variant_count"], 2)

    def test_episodes_without_pack_collapse_with_clean_title(self):
        cards = api._group_resources([
            item("剧名 S01E01", "u1"),
            item("剧名 S01E02 1080P", "u2"),
        ])
        self.assertEqual(len(cards), 1)
        card = cards[0]
        self.assertIn("单集可选", card["title"])
        self.assertEqual(card["episode_count"], 2)
        self.assertFalse(card["has_pack"])

    def test_different_seasons_stay_separate(self):
        cards = api._group_resources([
            item("剧名 第一季 S01E01", "u1"),
            item("剧名 第二季 S02E01", "u2"),
        ])
        self.assertEqual(len(cards), 2)

    def test_short_query_does_not_strip_prefix(self):
        # “我的三体”是另一部作品，不能因为查询词短就被前缀裁剪并进《三体》
        cards = api._group_resources([
            item("我的三体", "u1"),
            item("三体", "u2"),
        ], keyword="三体")
        self.assertEqual(len(cards), 2)


class UrlCanonicalTest(unittest.TestCase):
    def test_tracking_params_are_ignored(self):
        a = api._canonical_url(
            "https://pan.quark.cn/s/abc?entry=share&utm_source=x")
        b = api._canonical_url("http://pan.quark.cn/s/abc/")
        self.assertEqual(a, b)

    def test_pwd_is_ignored_for_dedup_but_preserved(self):
        a = api._canonical_url(
            "https://pan.quark.cn/s/abc?pwd=1234&from=x")
        b = api._canonical_url("https://pan.quark.cn/s/abc")
        self.assertEqual(a, b)
        ranked, _, _ = api._dedup_and_rank([], [
            item("剧名", "https://pan.quark.cn/s/abc?pwd=1234&from=x"),
            item("剧名", "https://pan.quark.cn/s/abc"),
        ], "剧名")
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0].get("password"), "1234")

    def test_magnet_trackers_ignored(self):
        a = api._canonical_url("magnet:?xt=urn:btih:ABC&tr=udp://a")
        b = api._canonical_url("magnet:?xt=urn:btih:ABC&tr=udp://b")
        self.assertEqual(a, b)

    def test_share_ids_are_case_sensitive(self):
        self.assertNotEqual(
            api._canonical_url("https://pan.quark.cn/s/abc"),
            api._canonical_url("https://pan.quark.cn/s/ABC"))

    def test_same_url_with_tracking_collapses(self):
        ranked, _, _ = api._dedup_and_rank([], [
            item("剧名", "https://pan.quark.cn/s/abc?entry=share"),
            item("剧名", "http://pan.quark.cn/s/abc/"),
        ], "剧名")
        self.assertEqual(len(ranked), 1)


class QualityRankingTest(unittest.TestCase):
    def test_pack_4k_beats_single_episode(self):
        ranked, _, _ = api._dedup_and_rank([], [
            item("剧名 S01E50", "u1"),
            item("剧名 全60集 4K 中字", "u2"),
        ], "剧名")
        self.assertEqual(ranked[0]["url"], "u2")

    def test_4k_beats_720p_for_same_title(self):
        ranked, _, _ = api._dedup_and_rank([], [
            item("剧名 720P", "u1"),
            item("剧名 4K", "u2"),
        ], "剧名")
        self.assertEqual(ranked[0]["url"], "u2")

    def test_cam_version_downranked(self):
        ranked, _, _ = api._dedup_and_rank([], [
            item("剧名 枪版", "u1"),
            item("剧名 1080P 中字", "u2"),
        ], "剧名")
        self.assertEqual(ranked[0]["url"], "u2")

    def test_pack_beats_verified_single_episode_for_main_card(self):
        pack = item("剧名 全60集", "u1", validity="")
        episode = item("剧名 S01E01", "u2", validity="ok")
        self.assertGreater(api._rep_score(pack), api._rep_score(episode))


class ComposeSourceListTest(unittest.TestCase):
    def test_no_spaces_in_source_lists(self):
        root = os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))
        path = os.path.join(root, "docker-compose.yml")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        for key in ("CHANNELS", "ENABLED_PLUGINS"):
            m = re.search(rf'^\s*{key}:\s*"([^"]+)"', text, re.M)
            self.assertIsNotNone(m, key)
            value = m.group(1)
            # YAML 折行会把 "a,b" 变成 "a, b"，PanSou 不 trim
            self.assertNotIn(", ", value, key)
            self.assertEqual(value, value.strip(), key)


if __name__ == "__main__":
    unittest.main(verbosity=2)
