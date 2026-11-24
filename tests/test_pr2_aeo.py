import csv
import datetime as dt
import json
import re
import shutil
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "trend_monitor"))
import main_aggregator as agg


class Pr2AeoTest(unittest.TestCase):
    """2本目のPR（AIの答えに選ばれやすい書き方）のテスト。

    対象は直す側だけ。比べる側（2246340）には何も出ないことを確かめる。
    """

    def setUp(self):
        self.root = Path(__file__).resolve().parent / (".tmp-pr2-aeo-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.addCleanup(self.remove_test_root)
        self.docs = self.root / "docs"
        self.docs.mkdir()
        self.csv = self.root / "data" / "trend_report.csv"
        self.csv.parent.mkdir()
        self.rows = []
        self.add_row("other", "2026-08-01", 0, kind="gadget_new", title="Gadget")
        # 直す側・セット商品（バンドル）と確かめられた2本のうち32470
        self.add_row("32470", "2026-09-01", 60, "4800円",
                     title="サイバーパンク2077 アルティメットエディション")
        self.add_row("32470", "2026-10-01", 60, "4800円",
                     title="サイバーパンク2077 アルティメットエディション")
        # 直す側・通常のゲーム。生成日（2026-10-02）にセール中
        self.add_row("100", "2026-09-10", 50, "1000円", title="Fixture Game")
        self.add_row("100", "2026-10-02", 40, "1200円", title="Fixture Game")
        # 比べる側
        self.add_row("2246340", "2026-09-05", 30, "5000円", title="Control Game")
        self.add_row("2246340", "2026-10-05", 30, "5000円", title="Control Game")
        self.save_csv()
        self.today = dt.date(2026, 10, 2)

    def remove_test_root(self):
        root = self.root.resolve()
        self.assertEqual(root.parent, Path(__file__).resolve().parent)
        self.assertTrue(root.name.startswith(".tmp-pr2-aeo-"))
        shutil.rmtree(root)

    def add_row(self, gid, day, discount, price="1000円", kind="game_sale",
                title=None, hour="16"):
        self.rows.append(dict(zip(agg.CSV_HEADER, [
            gid, kind, f"{discount}%OFF", title or "Title", price,
            f"https://store.steampowered.com/app/{gid}/", "Steam Store",
            f"{day} {hour}:00:00", "2000", str(discount),
        ])))

    def save_csv(self):
        with self.csv.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=agg.CSV_HEADER)
            writer.writeheader()
            writer.writerows(self.rows)

    def generate(self):
        return agg.write_game_pages(str(self.csv), str(self.docs), self.today)

    def generated_html(self, gid):
        return (self.docs / agg.game_rel_path(gid) / "index.html").read_text(encoding="utf-8")

    # --- リンク修正：セット商品の Steam リンク ---

    def test_store_url_helper_bundle_and_others(self):
        bundle = {"url": "https://store.steampowered.com/app/32470/"}
        self.assertEqual(agg.game_store_url("32470", bundle),
                         "https://store.steampowered.com/bundle/32470/")
        self.assertEqual(agg.game_store_url(78920, {"url": "https://store.steampowered.com/app/78920/"}),
                         "https://store.steampowered.com/bundle/78920/")
        # 直す側でもバンドルでないものは記録のまま
        self.assertEqual(agg.game_store_url("100", {"url": "https://store.steampowered.com/app/100/"}),
                         "https://store.steampowered.com/app/100/")
        # 比べる側は記録のまま
        self.assertEqual(agg.game_store_url("2246340", {"url": "https://store.steampowered.com/app/2246340/"}),
                         "https://store.steampowered.com/app/2246340/")

    def test_bundle_link_on_generated_pages(self):
        self.generate()
        bundle_page = self.generated_html("32470")
        self.assertIn("https://store.steampowered.com/bundle/32470/", bundle_page)
        self.assertNotIn("https://store.steampowered.com/app/32470/", bundle_page)
        self.assertIn("https://store.steampowered.com/app/100/", self.generated_html("100"))
        self.assertIn("https://store.steampowered.com/app/2246340/", self.generated_html("2246340"))


if __name__ == "__main__":
    unittest.main()
